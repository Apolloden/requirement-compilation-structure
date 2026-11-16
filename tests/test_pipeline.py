import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arcflash import config
from arcflash.check import CheckReport
from arcflash.harness import HarnessStatus, TestResult
from arcflash.llm import LLMError, Usage
from arcflash.pipeline import Budget, Tools, critical_output_tokens, good_enough, run
from arcflash.proc import ProcResult
from tests.fakes import FakeLLM, as_blocks
from tests.golden import golden_files, golden_plan
from tests.support import AGENT_ROOT, FIXTURES, SAFE_ENV

NOTES = FIXTURES / "specs" / "notes"
MAIN = "frontend/src/pages/m1.jsx"
TESTS = "tests/scenarios-1.spec.ts"


def report(passed=3, total=3, built=True, booted=True):
    tests = [TestResult(f"SCN-{i} t", "scenarios-1.spec.ts", "passed" if i < passed else "failed", error="" if i < passed else "Error: x")
             for i in range(total)]
    return CheckReport(static_issues=[], built=built, booted=booted, scenarios=tests if booted else [],
                       smoke=[TestResult("smoke /", "smoke.spec.ts", "passed")] if booted else [], browser_checked=booted)


GREEN, NOT_BOOTED, NOT_BUILT = report(), report(built=True, booted=False), report(built=False, booted=False)


def frontend_answer(marker=""):
    """The page module answer (Task 10: the shell is its own call)."""
    files = golden_files()
    return as_blocks({MAIN: files[MAIN] + (f"\n// {marker}\n" if marker else "")})


def shell_answer():
    files = golden_files()
    return as_blocks({k: files[k] for k in ("frontend/src/shared.jsx", "frontend/src/App.jsx")})


def golden_llm(attempts=1, plan=None, frontends=None, repairs=4):
    files = golden_files()
    return FakeLLM({
        "vision:": ["Layout: a list of notes."],
        "tests:": [as_blocks({TESTS: files[TESTS]})],
        "plan": plan if plan is not None else [json.dumps(golden_plan())] * attempts,
        "backend": [as_blocks({"backend/app.js": files["backend/app.js"]})] * attempts,
        "frontend:shell": [shell_answer()] * attempts,
        "frontend:m1": frontends or [frontend_answer()] * attempts,
        "repair:": ["no blocks"] * repairs,
    })


def tools(reports=(), install_rc=0, on_check=None):
    queue = list(reports)
    calls = []

    def check(ws, plan, ok):
        calls.append(ok)
        if on_check:
            on_check()
        return queue.pop(0)
    t = Tools(install_app=lambda ws: ProcResult(install_rc, "npm output", 1.0),
              prepare_harness=lambda out: HarnessStatus(True, "ok"), check=check)
    t.calls = calls
    return t


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "out"

    def run_quiet(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as self.printed:
            return run(*args, **kwargs)

    def test_happy_path_ships_attempt_one(self):
        llm = golden_llm()
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[GREEN]))
        self.assertEqual(result["shipped"]["source"], "a1-r0"); self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual((self.out / "frontend/src/pages/m1.jsx").read_text(), golden_files()["frontend/src/pages/m1.jsx"])
        self.assertTrue((self.out / ".arc/run.json").exists())
        saved = json.loads((self.out / ".arc/run.json").read_text())
        self.assertEqual(saved["shipped"], result["shipped"])
        self.assertEqual(saved["input"], {"requirements": 5, "leaves": 3, "scenarios": 3, "images": 1, "assets": 0})
        self.assertEqual(saved["attempts"][0]["rounds"][0]["label"], "a1-r0")
        self.assertEqual((saved["shipped"]["scenarios_passed"], saved["harness_ok"], saved["usage"]["calls"]), (3, True, len(llm.calls)))
        self.assertTrue((self.out / ".arc/plan-1.json").exists()); self.assertTrue((self.out / ".arc/digest.txt").exists())
        self.assertEqual(json.loads((self.out / ".arc/notes.json").read_text()), {"reference/notes.png": "Layout: a list of notes."})
        self.assertEqual((self.out / ".arc/harness/tests/scenarios-1.spec.ts").read_text(), golden_files()[TESTS])
        self.assertIn("shipped a1-r0", self.printed.getvalue())
        self.assertFalse((self.out / "frontend/src/pages/main.jsx").exists())   # no template page left beside the modules
        self.assertEqual(saved["agent"], "arczenith"); self.assertIn("[arczenith] shipped a1-r0", self.printed.getvalue())

    # Task 12: time measured in output tokens on the critical path, independent of the endpoint's speed.
    def test_critical_output_tokens_count_plan_the_longest_code_path_and_repairs(self):
        r = lambda purpose, out: {"purpose": purpose, "output": out}
        records = [r("vision:a.png", 200), r("tests:1", 4000), r("plan", 9000), r("plan+1", 1000),
                   r("backend", 12000), r("frontend:shell", 4000), r("frontend:m1", 6000), r("frontend:m2", 5000),
                   r("frontend:m2-again", 3000), r("frontend:m1+1", 500), r("repair:a1.1", 8000), r("repair:a1.1.fix", 300),
                   r("quick_fix:a1.2", 700),
                   r("plan", 7000), r("backend", 9000), r("frontend:shell", 2000), r("frontend:m1", 8000)]
        # attempt 1: 10000 + max(12000, 4000 + 8000) + 9000; attempt 2: 7000 + max(9000, 2000 + 8000)
        self.assertEqual(critical_output_tokens(records), 10000 + 12000 + 9000 + 7000 + 10000)

    def test_run_json_has_a_timeline_and_the_critical_tokens(self):
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(), tools=tools(reports=[report(passed=1), GREEN]))
        phases = [p["phase"] for p in result["timeline"]]
        for phase in ("notes", "plan a1", "code a1", "check a1-r0", "repair a1.1"):   # the fake repair edits nothing: no new check
            self.assertIn(phase, phases)
        self.assertTrue(all(0 <= p["start"] <= p["end"] for p in result["timeline"]))
        self.assertEqual(result["critical_output_tokens"], critical_output_tokens(result["calls"]))
        saved = json.loads((self.out / ".arc/run.json").read_text())
        self.assertEqual(saved["timeline"], result["timeline"])

    def test_restart_when_the_first_attempt_does_not_start(self):
        llm = golden_llm(attempts=2)
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[NOT_BOOTED, GREEN]))
        self.assertEqual(result["shipped"]["source"], "a2-r0")
        self.assertEqual(len([c for c in llm.calls if c["purpose"] == "plan"]), 2)
        temps = [c["temperature"] for c in llm.calls if c["purpose"] == "plan"]
        self.assertEqual(temps, [config.TEMPERATURE, config.RESTART_TEMPERATURE])

    def test_better_attempt_is_shipped(self):
        llm = golden_llm(attempts=2, frontends=[frontend_answer("first"), frontend_answer("second")])
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[report(passed=1), report(passed=0)]))
        self.assertTrue(result["shipped"]["source"].startswith("a1-"))
        self.assertIn("// first", (self.out / MAIN).read_text())
        self.assertEqual(len(result["attempts"]), 2)   # the restart produced an app: no further repairs of attempt 1

    def test_a_failed_restart_goes_back_to_repairing_the_best_version(self):
        # ArcLaser SO lane 2: attempt 1 stopped at 20/66 after 17 minutes (two rounds without gain), the restart's plan
        # was cut three times and then ran away, and the run shipped a1-r0 with 100 minutes unused.
        line = golden_files()[MAIN].splitlines()[0]
        fix = f"=== EDIT: {MAIN} ===\n<<<<<<< SEARCH\n{line}\n=======\n{line}\n// resumed\n>>>>>>> REPLACE\n=== END EDIT ===\n"
        llm = golden_llm(plan=[json.dumps(golden_plan()), LLMError("connection error")], repairs=0)
        llm.script["repair:"] = ["no blocks"] * config.MAX_ROUNDS_WITHOUT_GAIN + [fix]
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[report(passed=1), GREEN]))
        self.assertEqual(result["shipped"]["source"], "a1c-r1")
        self.assertIn("// resumed", (self.out / MAIN).read_text())
        self.assertEqual([a["attempt"] for a in result["attempts"]], [1, 2, "1c"])
        self.assertEqual(result["attempts"][-1]["resumed_from"], "a1-r0")
        resumed = [c for c in llm.calls if c["purpose"].startswith("repair:a1c")]
        self.assertIn("previous fix made the results worse", resumed[0]["user"])   # it is told to try something else

    def test_a_restart_refused_for_time_goes_back_to_repairing(self):
        # ArcLaser 12306 lane: attempt 1 ended at 46/142 after 62 minutes (two rounds without gain); a restart needs as
        # long as the first attempt took, so it was refused and 58 minutes went unused.
        now = [0.0]
        def jump(): now[0] = 120 * 60   # a restart needs 120 more minutes; the limit is 180
        line = golden_files()[MAIN].splitlines()[0]
        fix = f"=== EDIT: {MAIN} ===\n<<<<<<< SEARCH\n{line}\n=======\n{line}\n// resumed\n>>>>>>> REPLACE\n=== END EDIT ===\n"
        llm = golden_llm(repairs=0)
        llm.script["repair:"] = ["no blocks"] * config.MAX_ROUNDS_WITHOUT_GAIN + [fix]
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[report(passed=1), GREEN], on_check=jump),
                                clock=lambda: now[0])
        self.assertIn("restart", result["stop_reason"])
        self.assertEqual([a["attempt"] for a in result["attempts"]], [1, "1c"])
        self.assertEqual(result["shipped"]["source"], "a1c-r1")

    def test_template_is_shipped_when_nothing_builds(self):
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(attempts=2), tools=tools(reports=[NOT_BUILT, NOT_BUILT]))
        self.assertEqual(result["shipped"]["source"], "template")
        self.assertIn("<h1>Application</h1>", (self.out / "frontend/src/pages/main.jsx").read_text())

    def test_built_but_not_started_ships_the_best_built(self):
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(attempts=2), tools=tools(reports=[NOT_BOOTED, NOT_BUILT]))
        self.assertEqual((result["shipped"]["source"], result["shipped"]["built"], result["shipped"]["booted"]), ("a1-r0", True, False))

    def test_model_failure_still_leaves_a_working_template(self):
        llm = golden_llm(plan=[LLMError("platform down"), LLMError("platform down")])
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools())
        self.assertTrue(result["errors"]); self.assertTrue((self.out / "backend/server.js").exists())
        self.assertEqual(result["shipped"]["source"], "template")
        self.assertEqual(len(result["attempts"]), 2); self.assertIn("platform down", result["attempts"][0]["error"])

    def test_missing_key_ends_the_run_with_the_template(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            result = self.run_quiet(NOTES, self.out, llm=None, tools=tools())
        self.assertIn("OPENAI_API_KEY is not set", " ".join(result["errors"]))
        self.assertEqual(result["shipped"]["source"], "template"); self.assertTrue((self.out / "deploy.sh").exists())

    def test_budget_cap_scales_with_scenarios(self):
        # caps only; the repairs now run until the time budget (ArcLaser v2 graded SO spent ¥4.56 of ¥5.28 estimated)
        self.assertEqual(Budget(Usage(), 12).cap_cny, 6.0); self.assertAlmostEqual(Budget(Usage(), 142).cap_cny, 17.04)

    def test_budget_rules(self):
        now = [0.0]
        usage = Usage()
        b = Budget(usage, 12, clock=lambda: now[0])
        self.assertTrue(b.allows("repair") and b.allows("rescue") and b.allows("attempt")); self.assertIsNone(b.stop_reason())
        now[0] = config.MAX_RUN_SECONDS - config.ATTEMPT_RESERVE_SECONDS + 1
        self.assertTrue(b.allows("repair")); self.assertFalse(b.allows("attempt")); self.assertIn("time", b.stop_reason())
        now[0] = config.MAX_RUN_SECONDS + 60          # past the limit: only an app that does not start is still repaired
        self.assertFalse(b.allows("repair")); self.assertTrue(b.allows("rescue"))
        now[0] = config.RESCUE_UNTIL_SECONDS + 1
        self.assertFalse(b.allows("rescue"))
        now[0] = 0.0
        from arcflash.llm import Completion
        usage.add("x", Completion("", "stop", output_tokens=700_000))   # list ¥2.80, billed ¥3.92 ≥ 60% of ¥6
        self.assertTrue(b.allows("repair")); self.assertFalse(b.allows("attempt")); self.assertIn("cost", b.stop_reason())
        usage.add("y", Completion("", "stop", output_tokens=400_000))   # billed ¥6.16 ≥ ¥6
        self.assertFalse(b.allows("rescue"))           # the cost cap holds for a rescue too

    def test_a_restart_needs_as_long_as_the_first_attempt_took(self):
        now = [0.0]
        b = Budget(Usage(), 12, clock=lambda: now[0])
        b.attempt_seconds = 70 * 60                    # v3 Ctrip: 74 min to the first check
        now[0] = config.MAX_RUN_SECONDS - 70 * 60 - 1
        self.assertTrue(b.allows("attempt"))
        now[0] = config.MAX_RUN_SECONDS - 70 * 60 + 1
        self.assertFalse(b.allows("attempt")); self.assertIn("restart", b.stop_reason())

    def test_limits(self):
        self.assertEqual((config.MAX_RUN_SECONDS, config.RESCUE_UNTIL_SECONDS), (200 * 60, 215 * 60))

    def test_good_enough(self):
        self.assertTrue(good_enough(report(passed=2))); self.assertFalse(good_enough(report(passed=1)))
        self.assertFalse(good_enough(NOT_BOOTED)); self.assertTrue(good_enough(report(passed=0, total=0)))

    def test_no_restart_when_time_is_short(self):
        now = [0.0]
        def jump(): now[0] = config.MAX_RUN_SECONDS - 60
        llm = golden_llm(attempts=2)
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[report(passed=1)], on_check=jump), clock=lambda: now[0])
        self.assertEqual(len(result["attempts"]), 1); self.assertIn("time", result["stop_reason"])
        self.assertFalse([c for c in llm.calls if c["purpose"].startswith("repair")])

    def test_rescue_after_the_time_limit_when_nothing_builds(self):
        now = [0.0]
        def jump(): now[0] = config.MAX_RUN_SECONDS + 60
        llm = golden_llm(attempts=2)
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[NOT_BUILT], on_check=jump), clock=lambda: now[0])
        self.assertTrue([c for c in llm.calls if c["purpose"].startswith("repair")])
        self.assertEqual(len(result["attempts"]), 1)

    def test_the_wait_for_the_test_writer_is_recorded(self):
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(), tools=tools(reports=[GREEN]))
        self.assertIn("test_wait_seconds", result); self.assertIn("for the test writer", self.printed.getvalue())

    def test_tests_and_notes_are_made_once(self):
        llm = golden_llm(attempts=2)
        result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[report(passed=1), GREEN]))
        self.assertEqual(len(result["attempts"]), 2)
        self.assertEqual(len([c for c in llm.calls if c["purpose"].startswith("tests:")]), 1)
        self.assertEqual(len([c for c in llm.calls if c["purpose"].startswith("vision:")]), 1)
        self.assertEqual((self.out / ".arc/harness/tests/scenarios-1.spec.ts").read_text(), golden_files()[TESTS])

    def test_the_plan_starts_before_the_notes_finish(self):
        # v4: notes took 7 to 15 min per site on a slow day and the plan waited for all of them (evidence.md E5).
        import threading, time as _time
        events = []
        class SlowVision(type(golden_llm())):
            def describe_image(self, *args, **kwargs):
                _time.sleep(0.3)
                answer = super().describe_image(*args, **kwargs)
                events.append(("vision end", _time.monotonic()))
                return answer
            def complete_long(self, purpose, *args, **kwargs):
                events.append((purpose, _time.monotonic()))
                return super().complete_long(purpose, *args, **kwargs)
        llm = golden_llm(); llm.__class__ = SlowVision
        self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[GREEN]))
        times = dict(events)
        self.assertLess(times["plan"], times["vision end"])
        self.assertGreater(times["backend"], times["vision end"])              # code waits for the notes
        plan_call = next(c for c in llm.calls if c["purpose"] == "plan")
        self.assertNotIn("SCREENSHOT NOTES", plan_call["user"])
        self.assertIn("Layout: a list of notes.", next(c for c in llm.calls if c["purpose"] == "backend")["user"])

    def test_code_uses_the_notes_that_exist_after_the_wait(self):
        import time as _time
        class NeverDone(type(golden_llm())):
            def describe_image(self, *args, **kwargs):
                _time.sleep(0.5)
                return super().describe_image(*args, **kwargs)
        llm = golden_llm(); llm.__class__ = NeverDone
        with mock.patch.object(config, "NOTES_WAIT_SECONDS", 0.05):
            result = self.run_quiet(NOTES, self.out, llm=llm, tools=tools(reports=[GREEN]))
        self.assertEqual(result["shipped"]["source"], "a1-r0")
        backend = next(c for c in llm.calls if c["purpose"] == "backend")["user"]
        self.assertIn("(screenshot unavailable)", backend); self.assertNotIn("Layout: a list of notes.", backend)

    def test_failed_install_ships_the_unchecked_app(self):
        t = tools(install_rc=1)
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(), tools=t)
        self.assertEqual(t.calls, [])
        self.assertEqual(result["shipped"]["source"], "unchecked")
        self.assertEqual((self.out / "backend/app.js").read_text(), golden_files()["backend/app.js"])

    def test_a_crash_after_a_version_still_ships_that_version(self):
        def crash():
            if len(t.calls) == 2:
                raise RuntimeError("check crashed")
        t = tools(reports=[report(passed=1), GREEN], on_check=crash)
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(attempts=2), tools=t)
        self.assertIn("check crashed", " ".join(result["errors"]))
        self.assertEqual(result["shipped"]["source"], "a1-r0")
        self.assertEqual((self.out / MAIN).read_text(), golden_files()[MAIN])
        self.assertFalse((self.out / ".arc/check").exists())

    def test_a_crash_before_any_version_ships_the_template(self):
        def crash():
            raise RuntimeError("check crashed")
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(attempts=2), tools=tools(reports=[GREEN], on_check=crash))
        self.assertEqual(result["shipped"]["source"], "template")
        self.assertIn("<h1>Application</h1>", (self.out / "frontend/src/pages/main.jsx").read_text())   # the template's page

    def test_requirements_inside_the_output_folder(self):
        # The ARC-Bench layout: main.py /workspace/template/requirements --output-dir /workspace/template
        self.out.mkdir(); shutil.copytree(NOTES, self.out / "requirements")
        (self.out / ".arc").mkdir(); (self.out / ".arc" / "runner-events.jsonl").write_text("{}\n")
        result = self.run_quiet(self.out / "requirements", self.out, llm=golden_llm(), tools=tools(reports=[GREEN]))
        self.assertEqual((result["shipped"]["source"], result["errors"]), ("a1-r0", []))
        for rel in ("requirements/requirements.yaml", "requirements/reference/notes.png", ".arc/runner-events.jsonl",
                    "package.json", "deploy.sh", "backend/server.js", "frontend/src/pages/m1.jsx"):
            self.assertTrue((self.out / rel).exists(), rel)

    def test_an_output_path_that_is_a_file_ends_the_run(self):
        self.out.write_text("x")
        result = self.run_quiet(NOTES, self.out, llm=golden_llm(), tools=tools())
        self.assertTrue(result["errors"]); self.assertEqual(self.out.read_text(), "x")

    def test_main_exits_zero_on_bad_input(self):
        empty_dir = self.tmp / "empty"; empty_dir.mkdir()
        r = subprocess.run([sys.executable, "main.py", str(empty_dir), "-o", str(self.out)], cwd=AGENT_ROOT, env=SAFE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0); self.assertIn("requirements.yaml", r.stdout + r.stderr)
        self.assertTrue((self.out / "deploy.sh").exists())
        self.assertEqual(json.loads((self.out / ".arc/run.json").read_text())["shipped"]["source"], "template")

    def test_main_ignores_unknown_arguments(self):
        empty_dir = self.tmp / "empty"; empty_dir.mkdir()
        r = subprocess.run([sys.executable, "main.py", str(empty_dir), "-o", str(self.out), "--extra-flag"], cwd=AGENT_ROOT, env=SAFE_ENV,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0); self.assertIn("ignoring unknown arguments: --extra-flag", r.stdout)


if __name__ == "__main__":
    unittest.main()
