"""The Playwright harness in .arc/harness: install, page-check targets, test runs and report parsing."""
from __future__ import annotations

import base64
import json
import re
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


def _cli(harness: Path) -> list[str]:
    return ["node", str(Path(harness).resolve() / "node_modules" / "@playwright" / "test" / "cli.js")]


def prepare_harness(out: Path) -> HarnessStatus:
    """npm install in .arc/harness, then install Chromium (a no-op when it is already in the cache)."""
    harness = (Path(out) / ".arc" / "harness").resolve()
    try:
        install = proc.run([proc.npm(), "install", "--no-audit", "--no-fund"], harness, timeout=config.INSTALL_TIMEOUT_SECONDS)
    except FileNotFoundError as e:
        return HarnessStatus(False, str(e))
    if install.returncode != 0:
        return HarnessStatus(False, "npm install failed:\n" + proc.tail(install.output))
    browser = proc.run(_cli(harness) + ["install", "chromium"], harness, timeout=config.INSTALL_TIMEOUT_SECONDS)
    log = proc.tail(install.output, 20) + "\n" + proc.tail(browser.output, 20)
    return HarnessStatus(browser.returncode == 0, log)


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


def run_playwright(harness: Path, base_url: str, smoke_file: Path, *, timeout: float) -> tuple[list[TestResult], list[str], str]:
    harness, smoke_file = Path(harness).resolve(), Path(smoke_file).resolve()
    report = harness / "results.json"
    report.unlink(missing_ok=True)
    env = proc.local_env({"ARCFLASH_BASE_URL": base_url, "ARCFLASH_SMOKE": str(smoke_file),
                          "ARCFLASH_WORKERS": str(config.CHECK_WORKERS)})
    run = proc.run(_cli(harness) + ["test"], harness, timeout=timeout, env=env)
    if not report.is_file():
        return [], [f"Playwright wrote no report:\n{proc.tail(run.output, 30)}"], run.output
    try:
        results, load_errors = parse_report(json.loads(report.read_text(encoding="utf-8")))
    except ValueError as e:
        return [], [f"Playwright report could not be read: {e}"], run.output
    return results, load_errors, run.output
