"""Repair rounds: one call with all problems and the files, SEARCH/REPLACE edits, a full check, keep the best."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from arcflash import config
from arcflash.blocks import apply_edits, parse_edits, render_files
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


def repair_files(files: dict[str, str], plan: dict, report: CheckReport) -> dict[str, str]:
    """All app files plus the failing test files when they fit; otherwise only the implicated ones."""
    failing_tests = {f"tests/{t.file}" for t in report.scenarios if t.status != "passed"} & set(files)
    app = {k: v for k, v in files.items() if not k.startswith("tests/")}
    chosen = {**app, **{k: files[k] for k in sorted(failing_tests)}}
    if sum(len(v) for v in chosen.values()) <= config.REPAIR_CONTEXT_CHARS:
        return chosen
    paths = [urlparse(t.url).path or "/" for t in report.scenarios if t.status != "passed" and t.url]
    paths += [t.title.removeprefix("smoke ") for t in report.smoke if t.status != "passed"]
    keys = set(SHELL) | _modules_for_paths(plan, paths) | failing_tests
    named = report.build_log + "\n" + "\n".join(report.static_issues)   # a module named in a build error or static issue
    keys |= {k for k in app if k.startswith("frontend/src/pages/") and k.split("/")[-1] in named}
    return {k: files[k] for k in files if k in keys}


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
    fix = llm.complete_long(f"{purpose}.fix", system, context + "\n\n" + task, max_tokens=config.MAX_TOKENS["repair"])
    new, _ = apply_edits(new, parse_edits(fix.text))
    return new


def repair_loop(ws: Workspace, context: str, plan: dict, llm, first: CheckReport,
                check: Callable[[], CheckReport], budget, label: str) -> tuple[CheckReport, Path]:
    """Up to MAX_REPAIR_ROUNDS rounds; a round that is not better is undone. Returns the best report and snapshot."""
    system = load_prompt("rules")
    best, best_snapshot = first, ws.snapshot(f"{label}-r0", _meta(f"{label}-r0", first))
    note, without_gain = "", 0
    for n in range(1, config.MAX_REPAIR_ROUNDS + 1):
        if best.all_green() or without_gain >= config.MAX_ROUNDS_WITHOUT_GAIN or not budget.allows("repair"):
            break
        files = ws.read_generated()
        purpose = f"repair:{label}.{n}"
        task = fill(load_prompt("repair"), previous_note=note, files=render_files(repair_files(files, plan, best)),
                    problems=best.to_prompt())
        try:
            answer = llm.complete_long(purpose, system, context + "\n\n" + task, max_tokens=config.MAX_TOKENS["repair"])
            new = _apply(llm, purpose, system, context, files, answer.text)
        except LLMError:
            break
        if new == files:   # nothing applied: the check would repeat the last result
            without_gain += 1
            continue
        ws.write({k: v for k, v in new.items() if files.get(k) != v})
        report = check()
        snapshot = ws.snapshot(f"{label}-r{n}", _meta(f"{label}-r{n}", report))
        if report.rank() > best.rank():
            best, best_snapshot, note, without_gain = report, snapshot, "", 0
        else:
            ws.restore(best_snapshot)
            note, without_gain = WORSE_NOTE, without_gain + 1
    return best, best_snapshot
