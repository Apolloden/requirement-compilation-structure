"""The full local check of the whole app: static → syntax → build → start → page check → scenario tests."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from arcflash import config, proc
from arcflash.check_static import static_issues as find_static_issues
from arcflash.harness import TestResult, run_playwright, write_smoke_targets
from arcflash.proc import free_port
from arcflash.workspace import Workspace

SERVER_LOG_LINES = 40
LOG_LINES = 60
ERROR_LINES = 15
ARIA_CHARS = 2500
LOCATOR_LINE = re.compile(r"^\s*(?:- )?(?:waiting for |Locator: )")
TEST_CODE_LINES = 20
TITLES_PER_GROUP = 10


def _group_key(error: str) -> tuple[str, str]:
    """First error line plus the element it was about: Playwright's first line alone is often generic."""
    lines = [l.strip() for l in error.splitlines() if l.strip()]
    first = lines[0] if lines else ""
    locator = next((l for l in lines if LOCATOR_LINE.match(l)), "").removeprefix("- ")
    return first, locator


def _group_line(key: tuple[str, str]) -> str:
    return " | ".join(part for part in key if part)


def _plural(n: int) -> str:
    return f"{n} test" + ("" if n == 1 else "s")


def _test_code(test: TestResult, test_files: dict[str, str]) -> str:
    """TEST_CODE_LINES numbered lines of the test file around the failing line the error's stack names."""
    source = test_files.get(test.file)
    found = re.search(rf"{re.escape(test.file)}:(\d+)", test.error) if source else None
    if not found:
        return ""
    lines = source.splitlines()
    line = int(found.group(1))
    start = max(1, line - TEST_CODE_LINES // 2 + 1)
    end = min(len(lines), start + TEST_CODE_LINES - 1)
    numbered = "\n".join(f"{n:4d} | {lines[n - 1]}" for n in range(start, end + 1))
    return f"Test code ({test.file}, lines {start} to {end}):\n{numbered}"


@dataclass
class CheckReport:
    static_issues: list[str]
    built: bool
    build_log: str = ""
    booted: bool = False
    boot_log: str = ""
    server_log: str = ""
    smoke: list[TestResult] = field(default_factory=list)
    scenarios: list[TestResult] = field(default_factory=list)
    load_errors: list[str] = field(default_factory=list)
    browser_checked: bool = False
    seconds: float = 0.0

    @property
    def scenario_passed(self) -> int:
        return sum(t.status == "passed" for t in self.scenarios)

    @property
    def scenario_total(self) -> int:
        return len(self.scenarios)

    @property
    def smoke_passed(self) -> int:
        return sum(t.status == "passed" for t in self.smoke)

    def rank(self) -> tuple:
        return (int(self.built), int(self.booted), self.scenario_passed, self.smoke_passed, -len(self.static_issues))

    def all_green(self) -> bool:
        return (self.built and self.booted and not self.static_issues and not self.load_errors
                and all(t.status == "passed" for t in self.smoke + self.scenarios))

    def failure_groups(self) -> list[list[TestResult]]:
        """Failing scenario tests grouped by first error line and element, largest group first (stable)."""
        groups: dict[tuple[str, str], list[TestResult]] = {}
        for t in self.scenarios:
            if t.status != "passed":
                groups.setdefault(_group_key(t.error), []).append(t)
        return sorted(groups.values(), key=len, reverse=True)

    def focus_tests(self) -> list[TestResult]:
        """The failing tests of the FOCUS_GROUPS biggest groups: what a repair round aims at."""
        return [t for group in self.failure_groups()[:config.FOCUS_GROUPS] for t in group]

    def _scenario_section(self, test_files: dict[str, str]) -> str:
        groups = self.failure_groups()
        head = f"SCENARIO TESTS: {self.scenario_passed} of {self.scenario_total} passed."
        if groups:
            head += f" {len(groups)} failure group{'' if len(groups) == 1 else 's'}, largest first; fix the groups shown in full."
        lines = [head]
        if self.load_errors:
            lines.append("Test files that could not load:")
            lines += [f"- {e}" for e in self.load_errors]
        for n, tests in enumerate(groups[:config.FOCUS_GROUPS], 1):
            t = tests[0]
            lines.append(f"GROUP {n} ({_plural(len(tests))}): {_group_line(_group_key(t.error))}")
            lines.append(f"FAILED: {t.title} ({t.file})")
            if t.error:
                lines.append("\n".join(t.error.splitlines()[:ERROR_LINES]))
            if t.url:
                lines.append(f"URL at failure: {t.url}")
            if t.aria:
                lines.append("Page at failure (ARIA snapshot):\n" + t.aria[:ARIA_CHARS])
            code = _test_code(t, test_files)
            if code:
                lines.append(code)
            if len(tests) > 1:
                others = [o.title for o in tests[1:]]
                more = f" (and {len(others) - TITLES_PER_GROUP} more)" if len(others) > TITLES_PER_GROUP else ""
                lines.append("Same first error in: " + ", ".join(others[:TITLES_PER_GROUP]) + more)
        if len(groups) > config.FOCUS_GROUPS:
            lines.append("OTHER FAILURE GROUPS (one line each):")
            lines += [f"- ({_plural(len(g))}) {_group_line(_group_key(g[0].error))}" for g in groups[config.FOCUS_GROUPS:]]
        return "\n".join(lines)

    def to_prompt(self, max_chars: int = config.REPORT_CHARS, test_files: dict[str, str] | None = None) -> str:
        """The problem text of Appendix B.4, most important first, cut at max_chars. `test_files` (file name → source)
        adds the lines around each shown group's failing test line."""
        sections = []
        if not self.built:
            sections.append("BUILD FAILED (npm run build). Last lines:\n" + proc.tail(self.build_log, LOG_LINES))
        elif not self.booted:
            sections.append("THE SERVER DID NOT START. Last lines of its log:\n" + proc.tail(self.boot_log, LOG_LINES))
        if self.static_issues:
            sections.append("STATIC CHECK:\n" + "\n".join(f"- {issue}" for issue in self.static_issues))
        failed_smoke = [t for t in self.smoke if t.status != "passed"]
        if failed_smoke:
            sections.append("PAGE CHECK (each planned page opened directly):\n" + "\n".join(
                f"- {t.title}: " + "\n  ".join(t.error.splitlines()[:3]) for t in failed_smoke))
        if self.browser_checked and (self.scenarios or self.load_errors):
            sections.append(self._scenario_section(test_files or {}))
        if self.server_log.strip():
            sections.append(f"SERVER LOG (last {SERVER_LOG_LINES} lines):\n" + proc.tail(self.server_log, SERVER_LOG_LINES))
        text = "\n\n".join(sections)
        if len(text) > max_chars:
            note = f"(report cut at {max_chars} characters)"
            text = text[:max_chars - len(note) - 1] + "\n" + note
        return text

    def to_json(self) -> dict:
        data = asdict(self)
        data.update(scenario_passed=self.scenario_passed, scenario_total=self.scenario_total,
                    smoke_passed=self.smoke_passed, smoke_total=len(self.smoke), rank=list(self.rank()))
        return data


def run_check(ws: Workspace, plan: dict, *, harness_ok: bool) -> CheckReport:
    started = time.monotonic()
    issues = find_static_issues(ws.read_generated(), plan)
    if (ws.out / "backend" / "app.js").is_file():
        syntax = proc.run(["node", "--check", "backend/app.js"], ws.out, timeout=60)
        if syntax.returncode != 0:
            issues.append("backend/app.js has a syntax error:\n" + proc.tail(syntax.output, 20))
    report = CheckReport(static_issues=issues, built=False)
    build = proc.run([proc.npm(), "run", "build"], ws.out, timeout=config.BUILD_TIMEOUT_SECONDS)
    if build.returncode != 0:
        report.build_log = proc.tail(build.output, 60)
        report.seconds = time.monotonic() - started
        return report
    report.built = True

    ws.check_dir.mkdir(parents=True, exist_ok=True)
    database = ws.check_dir / "test.db"
    for suffix in ("", "-wal", "-shm"):
        database.with_name(database.name + suffix).unlink(missing_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log_path = ws.check_dir / "server.log"
    server = proc.start(["node", "backend/server.js"], ws.out, log_path=log_path,
                        env=proc.local_env({"PORT": str(port), "DATABASE_URL": str(database)}))
    try:
        report.booted = proc.wait_for_http(base + "/api/health", config.BOOT_TIMEOUT_SECONDS, server)
        if not report.booted:
            report.boot_log = proc.tail(log_path.read_text(encoding="utf-8", errors="replace"), 60)
        elif harness_ok:
            smoke_file = write_smoke_targets(ws.harness, plan)
            results, report.load_errors, _ = run_playwright(ws.harness, base, smoke_file,
                                                            timeout=config.BROWSER_RUN_TIMEOUT_SECONDS)
            report.smoke = [t for t in results if t.file == "smoke.spec.ts"]
            report.scenarios = [t for t in results if t.file != "smoke.spec.ts"]
            report.browser_checked = True
    finally:
        proc.stop(server)
    report.server_log = proc.tail(log_path.read_text(encoding="utf-8", errors="replace"), SERVER_LOG_LINES)
    report.seconds = time.monotonic() - started
    return report
