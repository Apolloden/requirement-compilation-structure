import json
import shutil
import tempfile
import unittest
from pathlib import Path

from arcflash.check import CheckReport
from arcflash.harness import TestResult, parse_report, write_smoke_targets
from tests.golden import golden_plan
from tests.support import FIXTURES

REPORT = FIXTURES / "playwright" / "report.json"


def report(built=True, booted=True, passed=3, total=3, issues=(), smoke_failed=0, load_errors=()):
    scenarios = [TestResult(f"SCN-{i} test {i}", "scenarios-1.spec.ts", "passed" if i < passed else "failed",
                            error="" if i < passed else f"Error: failure {i}") for i in range(total)]
    smoke = [TestResult("smoke /", "smoke.spec.ts", "failed" if smoke_failed else "passed", error="Error: 2 elements")]
    return CheckReport(static_issues=list(issues), built=built, booted=booted, smoke=smoke if booted else [],
                       scenarios=scenarios if booted else [], load_errors=list(load_errors), browser_checked=booted)


def report_with_everything():
    same = "Error: locator.click: Test timeout of 30000ms exceeded.\nCall log:\n  - waiting for getByRole('link', { name: 'Notes', exact: true })"
    scenarios = [TestResult(f"SCN-{i} same failure", "scenarios-1.spec.ts", "failed", error=same, url="http://127.0.0.1:1/",
                            aria='- link "Home"') for i in range(5)]
    scenarios.append(TestResult("SCN-9 other failure", "scenarios-1.spec.ts", "failed", error="Error: other\nmore", aria="x" * 3000))
    scenarios.append(TestResult("SCN-10 fine", "scenarios-1.spec.ts", "passed"))
    return CheckReport(static_issues=["missing file frontend/src/pages/more.jsx"], built=True, booted=True,
                       server_log="line\n" * 200, smoke=[TestResult("smoke /notes", "smoke.spec.ts", "failed",
                       error="Error: 2 elements with role link and name \"Notes\" (at most one allowed)\nmore detail")],
                       scenarios=scenarios, load_errors=["Error: tests/scenarios-2.spec.ts: SyntaxError"], browser_checked=True)


def grouped_report(sizes):
    """One failure group per size, in this order; group k fails on button Bk at line 40 of scenarios-1.spec.ts."""
    scenarios = [TestResult(f"SCN-{k}-{i} t", "scenarios-1.spec.ts", "failed",
                            error=f"Error: failure {k}\nCall log:\n  - waiting for getByRole('button', {{ name: 'B{k}' }})\n"
                                  f"    at /tmp/h/tests/scenarios-1.spec.ts:40:7", url=f"http://127.0.0.1:1/p{k}")
                 for k, size in enumerate(sizes) for i in range(size)]
    return CheckReport(static_issues=[], built=True, booted=True, scenarios=scenarios, browser_checked=True)


class CheckReportTests(unittest.TestCase):
    def test_parse_report_fixture(self):
        results, load_errors = parse_report(json.loads(REPORT.read_text()))
        self.assertEqual(sorted(r.status for r in results), ["failed", "passed"])
        failed = next(r for r in results if r.status == "failed")
        self.assertTrue(failed.url.startswith("http://127.0.0.1:")); self.assertIn("textbox", failed.aria)
        self.assertIn("SyntaxError", load_errors[0])
        self.assertEqual(failed.file, "scenarios-1.spec.ts")
        self.assertNotIn("\x1b[", failed.error)
        self.assertLessEqual(len(failed.error.splitlines()), 15)
        self.assertIn("Test timeout", failed.error)

    def test_rank_order(self):
        self.assertLess(report(built=True, booted=False).rank(), report(built=True, booted=True).rank())
        self.assertLess(report(passed=1).rank(), report(passed=3).rank())
        self.assertLess(report(built=False, booted=False).rank(), report(built=True, booted=False).rank())
        self.assertLess(report(issues=["a", "b"]).rank(), report(issues=["a"]).rank())

    def test_all_green(self):
        self.assertTrue(report().all_green())
        self.assertFalse(report(passed=2).all_green())
        self.assertFalse(report(issues=["x"]).all_green())
        self.assertFalse(report(load_errors=["x"]).all_green())
        self.assertFalse(report(smoke_failed=1).all_green())
        self.assertFalse(report(booted=False).all_green())

    def test_prompt_order_limits_and_grouping(self):
        text = report_with_everything().to_prompt(max_chars=2000)
        self.assertLess(text.index("STATIC CHECK:"), text.index("PAGE CHECK"))
        self.assertLess(text.index("PAGE CHECK"), text.index("SCENARIO TESTS:"))
        self.assertTrue(text.endswith("(report cut at 2000 characters)"))
        self.assertLessEqual(len(text), 2000)
        full = report_with_everything().to_prompt()
        self.assertEqual(full.count("FAILED: SCN-"), 2)
        self.assertIn("Same first error in: SCN-1 same failure, SCN-2 same failure, SCN-3 same failure, SCN-4 same failure", full)
        self.assertIn("SCENARIO TESTS: 1 of 7 passed. 2 failure groups, largest first; fix the groups shown in full.\nTest files that could not load:\n- Error: tests/scenarios-2.spec.ts: SyntaxError", full)
        self.assertIn("GROUP 1 (5 tests): Error: locator.click: Test timeout of 30000ms exceeded. | waiting for getByRole('link', "
                      "{ name: 'Notes', exact: true })\nFAILED: SCN-0 same failure (scenarios-1.spec.ts)\nError: locator.click", full)
        self.assertIn("URL at failure: http://127.0.0.1:1/\nPage at failure (ARIA snapshot):\n- link \"Home\"", full)
        self.assertNotIn("x" * 2501, full)
        self.assertIn("- smoke /notes: Error: 2 elements with role link", full)
        self.assertTrue(full.rstrip().endswith("line"))
        self.assertEqual(full.split("SERVER LOG (last 40 lines):\n")[1].count("line"), 40)

    def test_groups_are_sorted_by_test_count(self):
        r = grouped_report([1, 4, 2, 3, 1])
        self.assertEqual([len(g) for g in r.failure_groups()], [4, 3, 2, 1, 1])
        self.assertEqual(r.failure_groups()[3][0].title, "SCN-0-0 t")   # equal counts keep their first appearance

    def test_report_shows_the_three_biggest_groups_in_full_and_the_rest_in_one_line(self):
        # v4: the top one or two failing lookups held 37 to 72% of the failures (docs/6-arclaser/evidence.md 3.1).
        tests = "\n".join(f"// line {n}" for n in range(1, 61)) + "\n"
        text = grouped_report([1, 4, 2, 3, 1]).to_prompt(test_files={"scenarios-1.spec.ts": tests})
        self.assertIn("SCENARIO TESTS: 0 of 11 passed. 5 failure groups, largest first; fix the groups shown in full.", text)
        for n, size, k in ((1, 4, 1), (2, 3, 3), (3, 2, 2)):
            self.assertIn(f"GROUP {n} ({size} tests): Error: failure {k} | waiting for getByRole('button', {{ name: 'B{k}' }})\n"
                          f"FAILED: SCN-{k}-0 t (scenarios-1.spec.ts)", text)
        self.assertLess(text.index("GROUP 1 (4 tests)"), text.index("GROUP 2 (3 tests)"))
        self.assertIn("Test code (scenarios-1.spec.ts, lines 31 to 50):\n  31 | // line 31\n", text)
        self.assertIn("  40 | // line 40\n", text); self.assertNotIn("  51 | // line 51", text)
        self.assertIn("OTHER FAILURE GROUPS (one line each):\n- (1 test) Error: failure 0 | waiting for getByRole('button', "
                      "{ name: 'B0' })\n- (1 test) Error: failure 4 | waiting for getByRole('button', { name: 'B4' })", text)
        self.assertEqual(text.count("FAILED: "), 3)
        self.assertEqual([t.title for t in grouped_report([1, 4, 2, 3, 1]).focus_tests()][:2], ["SCN-1-0 t", "SCN-1-1 t"])

    def test_strict_mode_between_layout_and_page_is_marked_as_a_test_scope_problem(self):
        # Shape of the recorded v4 errors (evidence.md 3.2 and 6.4), names invented.
        error = ("Error: locator.click: Error: strict mode violation: getByRole('button', { name: 'Enter' }) resolved to 2 elements:\n"
                 "    1) <button type=\"button\">Enter</button> aka getByRole('banner').getByRole('button', { name: 'Enter' })\n"
                 "    2) <button type=\"submit\">Enter</button> aka getByRole('region', { name: 'Enter' }).getByRole('button', { name: 'Enter' })\n")
        nav = error.replace("getByRole('banner')", "getByRole('navigation', { name: 'Site menu' })")
        label = error.replace("getByRole('banner')", "getByLabel('Site menu')")
        for text in (error, nav, label):
            r = CheckReport(static_issues=[], built=True, booted=True, browser_checked=True,
                            scenarios=[TestResult("SCN-1 t", "scenarios-1.spec.ts", "failed", error=text)])
            self.assertIn("\nTEST SCOPE: one match is in the shared layout and one in the page. If the requirement acts inside a "
                          "named form or region, scope the test's lookup to it; if not, the grader looks in the whole page, so "
                          "the page must not show the layout's control with the same name (hide it on this page).",
                          r.to_prompt(layout_areas=["Site menu"]))

    def test_strict_mode_inside_the_page_is_not_marked(self):
        error = ("Error: strict mode violation: getByRole('button', { name: 'Remove' }) resolved to 2 elements:\n"
                 "    1) <button>Remove</button> aka getByRole('row', { name: 'First' }).getByRole('button')\n"
                 "    2) <button>Remove</button> aka getByRole('row', { name: 'Second' }).getByRole('button')\n")
        r = CheckReport(static_issues=[], built=True, booted=True, browser_checked=True,
                        scenarios=[TestResult("SCN-1 t", "scenarios-1.spec.ts", "failed", error=error)])
        self.assertNotIn("TEST SCOPE", r.to_prompt(layout_areas=["Site menu"]))

    def test_prompt_build_and_start_sections(self):
        r = CheckReport(static_issues=["x"], built=False, build_log="vite error\n" * 100)
        text = r.to_prompt()
        self.assertTrue(text.startswith("BUILD FAILED (npm run build). Last lines:\nvite error"))
        self.assertEqual(text.count("vite error"), 60)
        self.assertLess(text.index("BUILD FAILED"), text.index("STATIC CHECK:"))
        r = CheckReport(static_issues=[], built=True, booted=False, boot_log="Error: boom")
        self.assertTrue(r.to_prompt().startswith("THE SERVER DID NOT START. Last lines of its log:\nError: boom"))

    def test_to_json(self):
        data = report_with_everything().to_json()
        self.assertEqual((data["scenario_passed"], data["scenario_total"], data["smoke_passed"], data["smoke_total"]), (1, 7, 0, 1))
        self.assertEqual(data["scenarios"][0]["title"], "SCN-0 same failure")
        json.dumps(data)

    def test_smoke_targets_skip_parameter_routes_and_add_layout(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        targets = json.loads(write_smoke_targets(tmp, golden_plan()).read_text())
        self.assertEqual([t["route"] for t in targets], ["/", "/notes", "/notes/new"])
        self.assertIn({"role": "link", "name": "Notes"}, targets[1]["controls"])
        self.assertIn({"role": "region", "name": "Note list"}, targets[1]["controls"])


if __name__ == "__main__":
    unittest.main()


class PlaywrightChoiceTests(unittest.TestCase):
    """The harness uses the Playwright version whose Chromium is already installed (no download on the runner)."""

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.cache, True)

    def test_runner_version_when_nothing_is_installed(self):
        from arcflash.harness import choose_playwright_version
        self.assertEqual(choose_playwright_version(self.cache), "1.57.0")

    def test_the_installed_browser_decides(self):
        from arcflash.harness import choose_playwright_version
        (self.cache / "chromium_headless_shell-1248").mkdir()
        self.assertEqual(choose_playwright_version(self.cache), "1.64.1")
        (self.cache / "chromium_headless_shell-1200").mkdir()
        self.assertEqual(choose_playwright_version(self.cache), "1.57.0")   # the runner's version comes first

    def test_browsers_path_from_the_environment(self):
        from arcflash.harness import browsers_dir
        from unittest import mock
        import os
        with mock.patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(self.cache)}):
            self.assertEqual(browsers_dir(), self.cache)
