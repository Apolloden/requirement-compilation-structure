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
MAIN = "frontend/src/pages/m1.jsx"


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
        self.assertEqual(snap.name, "a1-r1"); self.assertIn("Fixed", (self.ws.out / "frontend/src/pages/m1.jsx").read_text())
        self.assertTrue(best.all_green())
        c = llm.calls[0]
        self.assertEqual((c["purpose"], c["system"], c["max_tokens"]), ("repair:a1.1", load_prompt("rules"), config.MAX_TOKENS["repair"]))
        self.assertTrue(c["user"].startswith("CTX\n\nTask: the local check of the application found the problems below."))
        self.assertIn("=== frontend/src/pages/m1.jsx ===\n", c["user"]); self.assertIn("SCENARIO TESTS: 1 of 3 passed.", c["user"])
        self.assertNotIn("Your previous fix", c["user"])
        meta = json.loads((self.ws.versions / "a1-r1" / "meta.json").read_text())
        self.assertEqual((meta["scenarios_passed"], meta["built"]), (3, True))

    def test_rounds_and_their_checks_are_on_the_timeline(self):
        from arcflash.pipeline import Timeline
        import time as _time
        timeline = Timeline(_time.monotonic, _time.monotonic())
        llm = FakeLLM({"repair:": [edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Fixed notebook.</p>")]})
        repair_loop(self.ws, "CTX", golden_plan(), llm, self.failing, lambda: self.green, Budget(True), "a1", timeline=timeline)
        self.assertEqual([p["phase"] for p in timeline.sorted()], ["repair a1.1", "check a1-r1"])

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
        self.assertIn("frontend/src/pages/m1.jsx: SEARCH text not found: <p>Not in the file.</p>", llm.calls[1]["user"])
        self.assertIn(golden_files()["frontend/src/pages/m1.jsx"], llm.calls[1]["user"])
        self.assertTrue(llm.calls[1]["user"].startswith("CTX\n\nThese edits could not be applied:"))
        self.assertIn("Fixed notebook.", (self.ws.out / MAIN).read_text())

    def test_a_repair_answer_that_hits_the_limit_is_applied_not_continued(self):
        # v4: a repair answer was continued twice to 36k tokens, 85 to 92% of it no-op pairs (evidence.md 2.5).
        from arcflash.blocks import RepeatGuard
        from arcflash.llm import Completion
        fixed = edit(MAIN, "      <p>A small public notebook.</p>", "      <p>Fixed notebook.</p>")
        llm = FakeLLM({"repair:": [Completion(text=fixed, finish_reason="length")]})
        best, snap = self.run_loop(llm, [self.green])
        self.assertEqual((snap.name, len(llm.calls)), ("a1-r1", 1))
        self.assertEqual(config.MAX_TOKENS["repair"], 8000)
        self.assertEqual((llm.calls[0]["continue_cut"], type(llm.calls[0]["stop_when"])), (False, RepeatGuard))

    def test_a_build_failure_gets_a_quick_fix_with_only_the_error_and_its_lines(self):
        # v4 Stack Overflow: one missing "}" got a full repair round of 36k tokens and 34 minutes (evidence.md 2.6).
        page = "frontend/src/pages/x.jsx"
        self.ws.write({page: "".join(f"const line{n} = {n};\n" for n in range(1, 201))})
        log = "".join(f"log line {n}\n" for n in range(1, 81)) + "file: /work/frontend/src/pages/x.jsx:120:5\nERROR: Expected \"}\"\n"
        broken = report(0, built=False, booted=False); broken.build_log = log
        llm = FakeLLM({"quick_fix:": [edit(page, "const line120 = 120;", "const line120 = 121;")]})
        best, snap = self.run_loop(llm, [self.green], first=broken)
        self.assertEqual(snap.name, "a1-r1")
        c = llm.calls[0]
        self.assertEqual((c["purpose"], c["max_tokens"], config.MAX_TOKENS["quick_fix"]), ("quick_fix:a1.1", 2000, 2000))
        self.assertTrue(c["user"].startswith("CTX\n\nTask: the application does not build."))
        self.assertIn("log line 43\n", c["user"]); self.assertNotIn("log line 42\n", c["user"])   # the last 40 log lines
        self.assertIn(f"=== {page}, lines 80 to 160 ===\n  80 | const line80 = 80;\n", c["user"])
        self.assertIn(" 160 | const line160 = 160;", c["user"]); self.assertNotIn("const line161 =", c["user"])
        self.assertNotIn("const line79 =", c["user"]); self.assertNotIn("=== backend/app.js", c["user"])
        self.assertEqual((c["continue_cut"], type(c["stop_when"]).__name__), (False, "RepeatGuard"))
        self.assertIn("const line120 = 121;", (self.ws.out / page).read_text())

    def test_a_start_failure_gets_a_quick_fix_around_the_backend_line(self):
        down = report(0, built=True, booted=False)
        down.boot_log = "Error: boom\n    at Object.<anonymous> (/work/backend/app.js:3:9)\n"
        llm = FakeLLM({"quick_fix:": ["no blocks"], "repair:": ["no blocks"]})
        self.run_loop(llm, [], first=down)
        c = llm.calls[0]
        self.assertTrue(c["user"].startswith("CTX\n\nTask: the application builds but the server does not start."))
        self.assertIn("=== backend/app.js, lines 1 to ", c["user"]); self.assertIn("Error: boom", c["user"])

    def test_a_build_log_without_a_line_gets_a_normal_round(self):
        broken = report(0, built=False, booted=False); broken.build_log = "something failed\n"
        llm = FakeLLM({"repair:": ["no blocks", "no blocks"]})
        self.run_loop(llm, [], first=broken)
        self.assertEqual(llm.calls[0]["purpose"], "repair:a1.1")

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

    def test_after_the_hard_stop_the_best_version_ships_without_a_request(self):
        from arcflash.llm import LLM
        from tests.fakes import FakeClient
        client = FakeClient([])
        now = [0.0]
        llm = LLM(client=client, sleep=lambda s: None, clock=lambda: now[0])
        now[0] = config.HARD_STOP_SECONDS + 60
        best, snap = self.run_loop(llm, [], budget={"repair", "rescue"})
        self.assertEqual((client.requests, snap.name, best.scenario_passed), ([], "a1-r0", 1))

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

    def test_repair_files_are_those_of_the_focus_groups(self):
        # Task 4: the model sees the files of the three biggest failure groups, not every file (evidence.md 3.1).
        from arcflash.harness import TestResult
        plan = copy.deepcopy(golden_plan())
        for m in ("more", "other", "rare"):
            plan["pages"].append({"id": f"{m}_a", "route": f"/{m}", "title": "x", "layout": True, "module": m,
                                  "reqs": [], "controls": [], "shows": [], "flows": []})
            plan["modules"].append({"id": m, "pages": [f"{m}_a"]})
        files = golden_files()
        for m in ("more", "other", "rare"):
            files[f"frontend/src/pages/{m}.jsx"] = "export function X() {}\n"
        files["tests/scenarios-2.spec.ts"] = "// other tests\n"
        def failing(k, n, path, spec):
            return [TestResult(f"SCN-{k}-{i}", spec, "failed", error=f"Error: e{k}", url=f"http://127.0.0.1:1{path}") for i in range(n)]
        r = report(0, total=0)
        r.scenarios = failing(1, 3, "/more", "scenarios-1.spec.ts") + failing(2, 2, "/other", "scenarios-1.spec.ts") \
            + failing(3, 2, "/notes", "scenarios-1.spec.ts") + failing(4, 1, "/rare", "scenarios-2.spec.ts")
        keys = set(repair_files(files, plan, r))
        self.assertEqual(keys, {"backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", MAIN,
                                "frontend/src/pages/more.jsx", "frontend/src/pages/other.jsx", "tests/scenarios-1.spec.ts"})
        r.static_issues = ['button "Go" (page rare_a) does not appear in the frontend code']
        self.assertIn("frontend/src/pages/rare.jsx", repair_files(files, plan, r))

    def test_large_apps_send_only_implicated_files(self):
        plan = copy.deepcopy(golden_plan())
        plan["pages"].append({"id": "more_a", "route": "/more/:id", "title": "x", "layout": True, "module": "more",
                              "reqs": [], "controls": [], "shows": [], "flows": []})
        plan["modules"].append({"id": "more", "pages": ["more_a"]})
        files = golden_files(); files["frontend/src/pages/more.jsx"] = "export function PageMoreA() {}\n"
        with mock.patch.object(config, "REPAIR_CONTEXT_CHARS", 10):
            keys = set(repair_files(files, plan, self.failing))
            self.assertIn("frontend/src/pages/m1.jsx", keys); self.assertNotIn("frontend/src/pages/more.jsx", keys)
            self.assertTrue({"backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", "tests/scenarios-1.spec.ts"} <= keys)
            on_more = report(1, url="http://127.0.0.1:1/more/7?x=1")
            keys = set(repair_files(files, plan, on_more))
            self.assertIn("frontend/src/pages/more.jsx", keys); self.assertNotIn("frontend/src/pages/m1.jsx", keys)


if __name__ == "__main__":
    unittest.main()
