import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arcflash import config
from arcflash.check import CheckReport
from arcflash.harness import TestResult
from arcflash.llm import LLMError
from arcflash.prompting import load_prompt
from arcflash.repair import repair_files, repair_loop
from arcflash.workspace import Workspace
from tests.fakes import FakeLLM
from tests.golden import golden_files, golden_plan
from tests.support import FIXTURES

NOTES = FIXTURES / "specs" / "notes"
MAIN = "frontend/src/pages/main.jsx"


def report(passed, total=3, built=True, booted=True, url="http://127.0.0.1:1/notes"):
    tests = [TestResult(f"SCN-{i} t", "scenarios-1.spec.ts", "passed" if i < passed else "failed",
                        error="" if i < passed else "Error: x", url="" if i < passed else url) for i in range(total)]
    return CheckReport(static_issues=[], built=built, booted=booted, scenarios=tests if booted else [],
                       smoke=[TestResult("smoke /", "smoke.spec.ts", "passed")] if booted else [], browser_checked=booted)


def edit(path, search, replace):
    return f"=== EDIT: {path} ===\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n=== END EDIT ===\n"


class Budget:
    def __init__(self, ok): self.ok = ok
    def allows(self, kind): return self.ok if isinstance(self.ok, bool) else kind in self.ok


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ws = Workspace(self.tmp / "out"); self.ws.init(NOTES, [])
        self.ws.reset_to_template(); self.ws.write(golden_files())
        self.green, self.failing = report(3), report(1)

    def run_loop(self, llm, reports, budget=True, first=None):
        queue = list(reports)
        self.checks = 0
        def check():
            self.checks += 1
            return queue.pop(0)
        return repair_loop(self.ws, "CTX", golden_plan(), llm, first or self.failing, check, Budget(budget), "a1")

    def test_green_first_report_makes_no_call(self):
        llm = FakeLLM({})
        best, snap = self.run_loop(llm, [], first=self.green)
        self.assertEqual((llm.calls, snap.name), ([], "a1-r0"))
        self.assertTrue((self.ws.versions / "a1-r0" / "files" / MAIN).is_file())

    def test_better_round_becomes_the_best(self):
        llm = FakeLLM({"repair:": [edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Fixed notebook.</p>")]})
        best, snap = self.run_loop(llm, [self.green])
        self.assertEqual(snap.name, "a1-r1"); self.assertIn("Fixed", (self.ws.out / "frontend/src/pages/main.jsx").read_text())
        self.assertTrue(best.all_green())
        c = llm.calls[0]
        self.assertEqual((c["purpose"], c["system"], c["max_tokens"]), ("repair:a1.1", load_prompt("rules"), config.MAX_TOKENS["repair"]))
        self.assertTrue(c["user"].startswith("CTX\n\nTask: the local check of the application found the problems below."))
        self.assertIn("=== frontend/src/pages/main.jsx ===\n", c["user"]); self.assertIn("SCENARIO TESTS: 1 of 3 passed.", c["user"])
        self.assertNotIn("Your previous fix", c["user"])
        meta = json.loads((self.ws.versions / "a1-r1" / "meta.json").read_text())
        self.assertEqual((meta["scenarios_passed"], meta["built"]), (3, True))

    def test_worse_round_is_undone_and_reported(self):
        llm = FakeLLM({"repair:": [edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Broken.</p>"),
                                   edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Fixed notebook.</p>")]})
        best, snap = self.run_loop(llm, [report(1, built=False, booted=False), self.green])
        self.assertEqual(snap.name, "a1-r2")
        self.assertIn("Your previous fix made the results worse and was undone", llm.calls[1]["user"])
        self.assertIn("Fixed notebook.", (self.ws.out / MAIN).read_text()); self.assertNotIn("Broken.", (self.ws.out / MAIN).read_text())

    def test_two_rounds_without_gain_stop_the_loop(self):
        e = edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Other.</p>")
        llm = FakeLLM({"repair:": [e, e, e]})
        best, snap = self.run_loop(llm, [report(1), report(0)])
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(snap.name, "a1-r0"); self.assertEqual((self.ws.out / MAIN).read_text(), golden_files()[MAIN])

    def test_edits_that_do_not_apply_get_one_follow_up(self):
        llm = FakeLLM({"repair:a1.1.fix": [edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Fixed notebook.</p>")],
                       "repair:": [edit(MAIN, "      <p>Not in the file.</p>", "      <p>x</p>")]})
        best, snap = self.run_loop(llm, [self.green])
        self.assertEqual(llm.calls[1]["purpose"], "repair:a1.1.fix")
        self.assertIn("These edits could not be applied", llm.calls[1]["user"])
        self.assertIn("frontend/src/pages/main.jsx: SEARCH text not found: <p>Not in the file.</p>", llm.calls[1]["user"])
        self.assertIn(golden_files()["frontend/src/pages/main.jsx"], llm.calls[1]["user"])
        self.assertTrue(llm.calls[1]["user"].startswith("CTX\n\nThese edits could not be applied:"))
        self.assertIn("Fixed notebook.", (self.ws.out / MAIN).read_text())

    def test_budget_stops_repairs(self):
        llm = FakeLLM({})
        self.run_loop(llm, [], budget=False); self.assertEqual(llm.calls, [])

    def test_past_the_time_limit_only_an_app_that_does_not_start_is_repaired(self):
        # v3: every site reached its first check after the time limit, so no round ran; Ctrip shipped the template
        # because one missing export broke the build (docs/5-arcflash-v4/plan.md R2).
        llm = FakeLLM({"repair:": ["no edits"] * 2})
        self.run_loop(llm, [], budget={"rescue"}); self.assertEqual(llm.calls, [])
        self.run_loop(llm, [], budget={"rescue"}, first=report(0, built=False, booted=False))
        self.assertEqual([c["purpose"] for c in llm.calls], ["repair:a1.1", "repair:a1.2"])

    def test_a_white_page_gets_a_rescue_round_too(self):
        # Review point 4: the backend answers /api/health, but the frontend crashes at render, so no page shows.
        white = report(0)
        white.smoke = [TestResult("smoke /", "smoke.spec.ts", "failed", error="Error: element not found")]
        llm = FakeLLM({"repair:": ["no edits"] * 2})
        self.run_loop(llm, [], budget={"rescue"}, first=white)
        self.assertEqual([c["purpose"] for c in llm.calls], ["repair:a1.1", "repair:a1.2"])

    def test_an_answer_without_edits_is_a_round_without_gain(self):
        llm = FakeLLM({"repair:": ["I cannot help.", "Still nothing."]})
        best, snap = self.run_loop(llm, [])
        self.assertEqual((len(llm.calls), self.checks, snap.name), (2, 0, "a1-r0"))

    def test_a_failed_repair_call_keeps_the_best(self):
        llm = FakeLLM({"repair:": [LLMError("busy")]})
        best, snap = self.run_loop(llm, [])
        self.assertEqual((snap.name, best.scenario_passed), ("a1-r0", 1))

    def test_a_test_file_can_be_corrected(self):
        llm = FakeLLM({"repair:": [edit("tests/scenarios-1.spec.ts", 'test("SCN-REQ-1.3-01 Confirm deletion", async ({ page }) => {',
                                        '// corrected\ntest("SCN-REQ-1.3-01 Confirm deletion", async ({ page }) => {')]})
        self.run_loop(llm, [self.green])
        self.assertIn("corrected", (self.ws.harness / "tests/scenarios-1.spec.ts").read_text())

    def test_small_apps_send_every_app_file_and_failing_tests(self):
        keys = set(repair_files(golden_files(), golden_plan(), self.failing))
        self.assertEqual(keys, set(golden_files()))
        self.assertNotIn("tests/scenarios-1.spec.ts", set(repair_files(golden_files(), golden_plan(), self.green)))

    def test_a_test_file_named_in_a_static_issue_is_sent(self):
        # Review finding 8: a passing test that looks a name up with the wrong role was never sent, so its static
        # issue could not be fixed and every remaining round was spent on it.
        green = report(3)
        green.static_issues = ['tests/scenarios-1.spec.ts looks up "Note list" as status; the plan and the requirements say region']
        self.assertIn("tests/scenarios-1.spec.ts", repair_files(golden_files(), golden_plan(), green))
        with mock.patch.object(config, "REPAIR_CONTEXT_CHARS", 10):
            self.assertIn("tests/scenarios-1.spec.ts", repair_files(golden_files(), golden_plan(), green))

    def test_large_apps_send_only_implicated_files(self):
        plan = copy.deepcopy(golden_plan())
        plan["pages"].append({"id": "more_a", "route": "/more/:id", "title": "x", "layout": True, "module": "more",
                              "reqs": [], "controls": [], "shows": [], "flows": []})
        plan["modules"].append({"id": "more", "pages": ["more_a"]})
        files = golden_files(); files["frontend/src/pages/more.jsx"] = "export function PageMoreA() {}\n"
        with mock.patch.object(config, "REPAIR_CONTEXT_CHARS", 10):
            keys = set(repair_files(files, plan, self.failing))
            self.assertIn("frontend/src/pages/main.jsx", keys); self.assertNotIn("frontend/src/pages/more.jsx", keys)
            self.assertTrue({"backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", "tests/scenarios-1.spec.ts"} <= keys)
            on_more = report(1, url="http://127.0.0.1:1/more/7?x=1")
            keys = set(repair_files(files, plan, on_more))
            self.assertIn("frontend/src/pages/more.jsx", keys); self.assertNotIn("frontend/src/pages/main.jsx", keys)


if __name__ == "__main__":
    unittest.main()
