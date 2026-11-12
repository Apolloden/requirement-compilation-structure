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
        self.assertIn("(4 more tests failed with the same first error line: SCN-1 same failure, SCN-2 same failure, "
                      "SCN-3 same failure, SCN-4 same failure)", full)
        self.assertIn("SCENARIO TESTS: 1 of 7 passed.\nTest files that could not load:\n- Error: tests/scenarios-2.spec.ts: SyntaxError", full)
        self.assertIn("FAILED: SCN-0 same failure (scenarios-1.spec.ts)\nError: locator.click", full)
        self.assertIn("URL at failure: http://127.0.0.1:1/\nPage at failure (ARIA snapshot):\n- link \"Home\"", full)
        self.assertNotIn("x" * 2501, full)
        self.assertIn("- smoke /notes: Error: 2 elements with role link", full)
        self.assertTrue(full.rstrip().endswith("line"))
        self.assertEqual(full.split("SERVER LOG (last 40 lines):\n")[1].count("line"), 40)

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
