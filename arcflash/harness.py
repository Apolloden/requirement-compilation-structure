"""The Playwright harness in .arc/harness: install, page-check targets, test runs and report parsing."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from arcflash import config, proc

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ARIA_CHARS = 2500
ERROR_LINES = 15
FAILED_STATUSES = {"failed", "timedOut", "interrupted"}   # all reported as "failed"; the error text keeps the detail


@dataclass
class HarnessStatus:
    ok: bool
    log: str


@dataclass
class TestResult:
    title: str
    file: str
    status: str
    error: str = ""
    url: str = ""
    aria: str = ""


def browsers_dir() -> Path:
    """Where Playwright keeps its browsers: PLAYWRIGHT_BROWSERS_PATH, else the platform's default cache."""
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and configured != "0":
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "ms-playwright"


def choose_playwright_version(cache: Path | None = None) -> str:
    """The first known version whose Chromium is already installed, so no browser download is needed."""
    cache = cache if cache is not None else browsers_dir()
    for version, build in config.PLAYWRIGHT_CHROMIUM.items():
        if (cache / f"chromium_headless_shell-{build}").is_dir() or (cache / f"chromium-{build}").is_dir():
            return version
    return config.PLAYWRIGHT_VERSION


def use_playwright_version(folder: Path, version: str) -> None:
    """Set @playwright/test in folder/package.json to the given exact version."""
    manifest = Path(folder) / "package.json"
    data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {"private": True}
    data.setdefault("devDependencies", {})["@playwright/test"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _cli(harness: Path) -> list[str]:
    return ["node", str(Path(harness).resolve() / "node_modules" / "@playwright" / "test" / "cli.js")]


def prepare_harness(out: Path) -> HarnessStatus:
    """npm install in .arc/harness, then install Chromium (a no-op when it is already in the cache)."""
    harness = (Path(out) / ".arc" / "harness").resolve()
    version = choose_playwright_version()
    use_playwright_version(harness, version)
    try:
        install = proc.run([proc.npm(), "install", "--no-audit", "--no-fund"], harness, timeout=config.INSTALL_TIMEOUT_SECONDS)
    except FileNotFoundError as e:
        return HarnessStatus(False, str(e))
    if install.returncode != 0:
        return HarnessStatus(False, "npm install failed:\n" + proc.tail(install.output))
    browser = proc.run(_cli(harness) + ["install", "chromium"], harness, timeout=config.BROWSER_INSTALL_TIMEOUT_SECONDS)
    log = f"@playwright/test {version}\n" + proc.tail(install.output, 20) + "\n" + proc.tail(browser.output, 20)
    return HarnessStatus(browser.returncode == 0, log)


def write_named(harness: Path, role_names: list[tuple[str, str]]) -> None:
    """tests/named.ts: the names the requirements give to status and alert elements. The helpers look these up by
    name and every other message by its text (a status or alert takes its name only from aria-label)."""
    named: dict[str, list[str]] = {"status": [], "alert": []}
    for role, name in role_names:
        if role in named and name not in named[role]:
            named[role].append(name)
    path = Path(harness) / "tests" / "named.ts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// Names the requirements give to status and alert elements, written from the requirements.\n"
                    f"export const NAMED: Record<string, string[]> = {json.dumps(named, ensure_ascii=False)};\n",
                    encoding="utf-8")


def write_smoke_targets(harness: Path, plan: dict) -> Path:
    """Planned pages without a :parameter, with their named controls (plus the layout's) of the smoke roles."""
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    layout_controls = [c for c in layout.get("controls", []) if isinstance(c, dict)]
    targets = []
    for page in plan.get("pages", []):
        if not isinstance(page, dict) or not isinstance(page.get("route"), str) or ":" in page["route"]:
            continue
        controls = []
        for c in [c for c in page.get("controls", []) if isinstance(c, dict)] + (layout_controls if page.get("layout") else []):
            entry = {"role": c.get("role"), "name": c.get("name")}
            if entry["role"] in config.SMOKE_ROLES and isinstance(entry["name"], str) and entry["name"] and entry not in controls:
                controls.append(entry)
        targets.append({"route": page["route"], "controls": controls})
    path = Path(harness) / "smoke.json"
    path.write_text(json.dumps(targets, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _clean(text: str) -> str:
    return ANSI.sub("", text or "")


def _walk(suite: dict):
    for spec in suite.get("specs", []):
        yield spec
    for child in suite.get("suites", []):
        yield from _walk(child)


def _context(result: dict) -> tuple[str, str]:
    for attachment in result.get("attachments", []):
        if attachment.get("name") == "failure-context" and attachment.get("body"):
            try:
                data = json.loads(base64.b64decode(attachment["body"]).decode("utf-8"))
                return str(data.get("url", "")), str(data.get("aria", ""))[:ARIA_CHARS]
            except (ValueError, TypeError):
                return "", ""
    return "", ""


def parse_report(data: dict) -> tuple[list[TestResult], list[str]]:
    """Each test's last result, with its first error lines and failure context; top-level errors are load errors."""
    results = []
    for suite in data.get("suites", []):
        for spec in _walk(suite):
            for test in spec.get("tests", []):
                runs = test.get("results") or [{}]
                last = runs[-1]
                errors = last.get("errors") or ([last["error"]] if last.get("error") else [])
                message = "\n".join(_clean(e.get("message", "")) for e in errors if isinstance(e, dict)).strip()
                url, aria = _context(last)
                status = last.get("status", test.get("status", "unknown"))
                results.append(TestResult(title=spec.get("title", ""), file=Path(spec.get("file", "")).name,
                                          status="failed" if status in FAILED_STATUSES else status,
                                          error="\n".join(message.splitlines()[:ERROR_LINES]), url=url, aria=aria))
    load_errors = [_clean(e.get("message", "")).strip() for e in data.get("errors", []) if isinstance(e, dict)]
    return results, [e for e in load_errors if e]


LOAD_ERROR_FILE = re.compile(r"(?:^|[\\/\s])((?:scenarios|smoke)[\w.-]*\.spec\.ts)\b")


def unloadable_files(load_errors: list[str]) -> list[str]:
    """The test files Playwright's load errors name (first line of each error), in order."""
    names: list[str] = []
    for error in load_errors:
        found = LOAD_ERROR_FILE.search(error.splitlines()[0] if error else "")
        if found and found.group(1) not in names:
            names.append(found.group(1))
    return names


def _file_filters(names: list[str]) -> list[str]:
    return [re.escape(name) + "$" for name in names]


def load_errors_of(harness: Path, names: list[str], *, timeout: float = 120) -> list[str]:
    """Load (list, no browser) only these test files; the load errors they raise. A few seconds."""
    harness = Path(harness).resolve()
    run = proc.run(_cli(harness) + ["test", "--list", "--reporter=json"] + _file_filters(names), harness, timeout=timeout,
                   env=proc.local_env({}))
    start = run.output.find("{")
    try:
        data = json.loads(run.output[start:]) if start >= 0 else {}
    except ValueError:
        return [] if run.returncode == 0 else [proc.tail(run.output, 20)]
    return parse_report(data)[1]


def _run_report(harness: Path, report: Path, args: list[str], timeout: float, env: dict) -> tuple[list[TestResult], list[str], str]:
    report.unlink(missing_ok=True)
    run = proc.run(_cli(harness) + ["test"] + args, harness, timeout=timeout, env=env)
    if not report.is_file():
        return [], [f"Playwright wrote no report:\n{proc.tail(run.output, 30)}"], run.output
    try:
        results, load_errors = parse_report(json.loads(report.read_text(encoding="utf-8")))
    except ValueError as e:
        return [], [f"Playwright report could not be read: {e}"], run.output
    return results, load_errors, run.output


def run_playwright(harness: Path, base_url: str, smoke_file: Path, *, timeout: float) -> tuple[list[TestResult], list[str], str]:
    """Every test file; when some files do not load (Playwright then runs no test at all), the others once more, so a
    file that does not parse costs its own tests only. ArcLaser v3 12306: a duplicate const in one generated file made
    every check report 0 of 0 and 25 repair rounds ran blind (graded 65/142, v2 101). The load errors stay reported."""
    harness, smoke_file = Path(harness).resolve(), Path(smoke_file).resolve()
    report = harness / "results.json"
    env = proc.local_env({"ARCFLASH_BASE_URL": base_url, "ARCFLASH_SMOKE": str(smoke_file),
                          "ARCFLASH_WORKERS": str(config.CHECK_WORKERS)})
    results, load_errors, output = _run_report(harness, report, [], timeout, env)
    broken = unloadable_files(load_errors)
    if broken and not results:
        keep = [p.name for p in sorted((harness / "tests").glob("*.spec.ts")) if p.name not in broken]
        if keep:
            results, more_errors, output = _run_report(harness, report, _file_filters(keep), timeout, env)
            load_errors = load_errors + [e for e in more_errors if e not in load_errors]
    return results, load_errors, output
