"""Repair rounds: one call aimed at the biggest failure groups and their files, SEARCH/REPLACE edits, a full check, keep the best."""
from __future__ import annotations

import re
from contextlib import nullcontext
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from arcflash import config
from arcflash.blocks import RepeatGuard, apply_edits, parse_edits, render_files
from arcflash.check import CheckReport
from arcflash.llm import LLMError
from arcflash.prompting import fill, load_prompt
from arcflash.workspace import Workspace

SHELL = ("backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx")
WORSE_NOTE = "- Your previous fix made the results worse and was undone. Try a different fix.\n"


def _route_pattern(route: str) -> re.Pattern:
    parts = [("[^/]+" if s.startswith(":") else re.escape(s)) for s in route.split("/")]
    return re.compile("/".join(parts).rstrip("/") + "/?")


def _modules_for_paths(plan: dict, paths: list[str]) -> set[str]:
    owners = set()
    for page in plan.get("pages", []):
        if not isinstance(page, dict) or not isinstance(page.get("route"), str):
            continue
        pattern = _route_pattern(page["route"])
        if any(pattern.fullmatch(path) for path in paths):
            owners.add(f"frontend/src/pages/{page.get('module')}.jsx")
    return owners


LOG_PLACE = re.compile(r"((?:frontend/)?src/[\w/.-]+\.jsx?|backend/app\.js):(\d+)")
PAGE_NAMED = re.compile(r"\(page ([a-z][a-z0-9_]*)\)")


def _named_modules(app: dict[str, str], plan: dict, report: CheckReport) -> set[str]:
    """Page modules a build error or static issue names, by file name or by "(page <id>)"."""
    # a name the code never shows is often a value from the database: it does not pull its module into every round
    named = report.build_log + "\n" + "\n".join(i for i in report.static_issues if "does not appear in the frontend code" not in i)
    keys = {k for k in app if k.startswith("frontend/src/pages/") and k.split("/")[-1] in named}
    pages = set(PAGE_NAMED.findall(named))
    keys |= {f"frontend/src/pages/{p.get('module')}.jsx" for p in plan.get("pages", [])
             if isinstance(p, dict) and p.get("id") in pages}
    return keys & set(app)


def repair_files(files: dict[str, str], plan: dict, report: CheckReport) -> dict[str, str]:
    """The files a round needs. A running app: the shell, the page modules the FOCUS_GROUPS biggest failure groups ran
    on, modules named in static issues, and the groups' test files (Task 4). An app that does not build or start: every
    app file when they fit, otherwise the shell and the named modules."""
    app = {k: v for k, v in files.items() if not k.startswith("tests/")}
    named_tests = {k for k in files if k.startswith("tests/") and any(i.startswith(k + " ") for i in report.static_issues)}
    if not report.booted:
        if sum(len(v) for v in app.values()) <= config.REPAIR_CONTEXT_CHARS:
            keys = set(app) | named_tests
        else:
            keys = set(SHELL) | _named_modules(app, plan, report) | named_tests
        return {k: files[k] for k in files if k in keys}
    focus = report.focus_tests()
    tests = ({f"tests/{t.file}" for t in focus} & set(files)) | named_tests
    paths = [urlparse(t.url).path or "/" for t in focus if t.url]
    paths += [t.title.removeprefix("smoke ") for t in report.smoke if t.status != "passed"]
    modules = (_modules_for_paths(plan, paths) | _named_modules(app, plan, report)) & set(app)
    if not modules and sum(len(v) for v in app.values()) <= config.REPAIR_CONTEXT_CHARS:
        modules = {k for k in app if k.startswith("frontend/src/pages/")}   # no URL to go by: every module that fits
    keys = set(SHELL) | modules | tests
    return {k: files[k] for k in files if k in keys}


def _layout_areas(plan: dict) -> list[str]:
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    return [str(c["name"]) for c in layout.get("controls", []) if isinstance(c, dict) and c.get("name")
            and c.get("role") in ("navigation", "region", "banner", "complementary")]


def quick_fix_task(files: dict[str, str], report: CheckReport) -> str | None:
    """For an app that does not build (or builds but does not start): the log tail and the lines around each place it
    names (file:line). None when the log names no generated file; the round is then a normal one (Task 5)."""
    if report.built and report.booted:
        return None
    what, log = ("build", report.build_log) if not report.built else ("start", report.boot_log)
    places = []
    for m in LOG_PLACE.finditer(log):
        path = m.group(1) if m.group(1).startswith(("frontend/", "backend/")) else "frontend/" + m.group(1)
        if path in files and (path, int(m.group(2))) not in places:
            places.append((path, int(m.group(2))))
        if what == "start" and places:
            break   # the first place in a start log is where the server threw
    if not places:
        return None
    n = config.QUICK_FIX_LOG_LINES
    code = []
    for path, line in places:
        lines = files[path].splitlines()
        start, end = max(1, line - n), min(len(lines), line + n)
        code.append(f"=== {path}, lines {start} to {end} ===\n"
                    + "".join(f"{k:4d} | {lines[k - 1]}\n" for k in range(start, end + 1)))
    return fill(load_prompt("quick_fix"), what="the application does not build." if what == "build"
                else "the application builds but the server does not start.",
                log=proc_tail(log, n), code="\n".join(code).rstrip("\n"))


def proc_tail(text: str, lines: int) -> str:
    return "\n".join(text.rstrip("\n").splitlines()[-lines:])


def shows_pages(report: CheckReport) -> bool:
    """The server started and at least one page rendered (a frontend that crashes at render fails every smoke test)."""
    return report.booted and not (report.smoke and report.smoke_passed == 0)


def _meta(label: str, report: CheckReport) -> dict:
    return {"label": label, "rank": list(report.rank()), "built": report.built, "booted": report.booted,
            "scenarios_passed": report.scenario_passed, "scenarios_total": report.scenario_total,
            "smoke_passed": report.smoke_passed, "smoke_total": len(report.smoke),
            "static_issues": len(report.static_issues), "all_green": report.all_green(), "report": report.to_json()}


def _apply(llm, purpose: str, system: str, context: str, files: dict[str, str], answer: str) -> dict[str, str]:
    """Apply the answer's edits; edits that fail get one follow-up call with the current content of their files."""
    new, problems = apply_edits(files, parse_edits(answer))
    if not problems:
        return new
    targets = list(dict.fromkeys(p.split(": ", 1)[0] for p in problems))
    task = fill(load_prompt("edit_fix"), problems="\n".join(f"- {p}" for p in problems),
                files=render_files({k: new[k] for k in targets if k in new}))
    fix = llm.complete_long(f"{purpose}.fix", system, context + "\n\n" + task, max_tokens=config.MAX_TOKENS["repair"],
                            stop_when=RepeatGuard(), continue_cut=False)
    new, _ = apply_edits(new, parse_edits(fix.text))
    return new


def repair_loop(ws: Workspace, context: str, plan: dict, llm, first: CheckReport,
                check: Callable[[], CheckReport], budget, label: str, timeline=None) -> tuple[CheckReport, Path]:
    """Up to MAX_REPAIR_ROUNDS rounds; a round that is not better is undone. Returns the best report and snapshot.
    A booted app needs budget kind "repair"; one that does not start needs only "rescue"."""
    system = load_prompt("rules")
    span = timeline.span if timeline is not None else (lambda phase: nullcontext())
    best, best_snapshot = first, ws.snapshot(f"{label}-r0", _meta(f"{label}-r0", first))
    note, without_gain, quick_tried = "", 0, False   # one quick fix per failure; if it gains nothing, normal rounds follow
    for n in range(1, config.MAX_REPAIR_ROUNDS + 1):
        # past the time limit only an app that shows nothing is still repaired: it would score 0 (v4 plan R2)
        kind = "repair" if shows_pages(best) else "rescue"
        if best.all_green() or without_gain >= config.MAX_ROUNDS_WITHOUT_GAIN or not budget.allows(kind):
            break
        files = ws.read_generated()
        quick = None if quick_tried else quick_fix_task(files, best)
        if quick is not None:   # a build or start error with a named line: a 2k-token fix, not a full round (E6)
            purpose, task, tokens, quick_tried = f"quick_fix:{label}.{n}", quick, config.MAX_TOKENS["quick_fix"], True
        else:
            purpose, tokens = f"repair:{label}.{n}", config.MAX_TOKENS["repair"]
            tests = {k.removeprefix("tests/"): v for k, v in files.items() if k.startswith("tests/")}
            task = fill(load_prompt("repair"), previous_note=note, files=render_files(repair_files(files, plan, best)),
                        problems=best.to_prompt(test_files=tests, layout_areas=_layout_areas(plan)))
        try:
            with span(f"{purpose.split(':')[0].replace('_', ' ')} {label}.{n}"):
                answer = llm.complete_long(purpose, system, context + "\n\n" + task, max_tokens=tokens,
                                           stop_when=RepeatGuard(), continue_cut=False)
                new = _apply(llm, purpose, system, context, files, answer.text)
        except LLMError:
            break
        if new == files:   # nothing applied: the check would repeat the last result
            without_gain += 1
            continue
        ws.write({k: v for k, v in new.items() if files.get(k) != v})
        with span(f"check {label}-r{n}"):
            report = check()
        snapshot = ws.snapshot(f"{label}-r{n}", _meta(f"{label}-r{n}", report))
        if report.rank() > best.rank():
            best, best_snapshot, note, without_gain, quick_tried = report, snapshot, "", 0, False
        else:
            ws.restore(best_snapshot)
            note, without_gain = WORSE_NOTE, without_gain + 1
    return best, best_snapshot
