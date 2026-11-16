"""ArcZenith: the fixes found in the graded ArcLaser v3 run of 2026-11-16 and the v2 builds (docs/9-arczenith/README.md)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from arcflash import config
from arcflash.check import CheckReport, _group_key
from arcflash.check_static import text_control_issues
from arcflash.harness import TestResult, run_playwright, unloadable_files, write_named
from arcflash.plan import settle_plan
from arcflash.prompting import load_prompt
from arcflash.proc import ProcResult
from arcflash.repair import fix_unloadable_tests, repair_loop, scenarios_done
from arcflash.spec import load_spec, prose_role_names
from arcflash.workspace import Workspace
from tests.fakes import FakeLLM
from tests.golden import golden_files, golden_plan
from tests.support import FIXTURES

AGENT_ROOT = Path(__file__).resolve().parents[1]

SPEC = '''id: ROOT
name: Shop
description:
  text: A small shop.
children:
- id: REQ-1
  name: Account overview
  description:
    text: |-
      Clicking "My account"(role=link) displays "Information"(role=link) and "Addresses"(role=link) entry points.

      Account overview is a named region.
  scenarios:
  - id: SCN-REQ-1-01
    name: Open addresses
    steps:
    - id: STEP-1
      Given: The buyer has signed in.
      When: Open Addresses from Account overview.
      Then: The addresses open.
  children: []
- id: REQ-2
  name: Order history
  description:
    text: |-
      Clicking "Order history and details"(role=link) shows the orders. Order history is a named region.
  scenarios:
  - id: SCN-REQ-2-01
    name: Read orders
    steps:
    - id: STEP-2
      Given: The buyer has signed in.
      When: Open Order history and details from My account.
      Then: Order history lists the orders.
  children: []
'''


def plan_for():
    return {"app": {"title": "Shop"}, "auth": {"accounts": False}, "tables": [], "api": [], "layout": {"controls": []},
            "pages": [{"id": "account", "route": "/account", "layout": True, "reqs": ["REQ-1", "REQ-2"],
                       "controls": [{"role": "link", "name": "Information", "to": "/account/information", "when": "always"},
                                    {"role": "link", "name": "Addresses", "to": "/account/addresses", "when": "always"},
                                    {"role": "link", "name": "Order history and details", "to": "/account/orders",
                                     "when": "always"}]},
                      {"id": "account_orders", "route": "/account/orders", "layout": True, "reqs": ["REQ-2"], "controls": []}]}


def results(names: list[str], errors: list[str] | None = None) -> dict:
    suites = [{"title": n, "file": n, "specs": [{"title": f"SCN-{n} t", "file": n, "tests": [{"results": [{"status": "passed"}]}]}]}
              for n in names]
    return {"suites": suites, "errors": [{"message": e} for e in errors or []]}


class ArcZenithTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)

    # ---- areas the text declares in words, and "from" -----------------------------------------------------------------

    # PrestaShop (v3 graded 41/86): "Account overview is a named region" was not in the plan, and the links the scenarios
    # open "from Account overview" were rendered outside it; 29 of 31 signed-in grader tests timed out.
    def test_areas_declared_in_words_are_read(self):
        corpus = ("Account overview is a named region. Open Addresses from Account overview.\n"
                  "Breadcrumb and Subcategories are named regions. Breadcrumb shows Home; Subcategories lists Men.\n"
                  "Order summary and checkout steps Personal Information and Payment are named regions.\n"
                  "Click Continue in Personal Information, then Payment, then read Order summary.\n"
                  "The selected-train information is a named region Train Information, containing the train.\n"
                  "Train Information shows G1001.\n"
                  "Badge notification is a named status containing its message. The Badge notification appears.\n"
                  "Add traveler and Edit traveler forms are named regions. Click Add traveler.\n")
        self.assertEqual(prose_role_names(corpus), [
            ("region", "Account overview"), ("region", "Breadcrumb"), ("region", "Subcategories"),
            ("region", "Order summary"), ("region", "Personal Information"), ("region", "Payment"),
            ("region", "Train Information"), ("status", "Badge notification"), ("region", "Add traveler")])
        # "The selected-train information" (an article) and "Edit traveler forms" (named nowhere else) are not names

    def test_a_declared_area_is_planned_where_it_is_declared_and_from_places_controls_in_it(self):
        (self.tmp / "requirements.yaml").write_text(SPEC, encoding="utf-8")
        spec = load_spec(self.tmp)
        self.assertIn(("region", "Account overview"), spec.role_names); self.assertIn(("region", "Order history"), spec.role_names)
        plan = plan_for()
        settle_plan(plan, spec)
        account, orders = plan["pages"]
        names = {c["name"]: c for c in account["controls"]}
        self.assertEqual(names["Account overview"]["role"], "region")
        self.assertEqual(names["Addresses"].get("in"), "Account overview")       # "Open Addresses from Account overview"
        self.assertNotIn("in", names["Order history and details"])                # "from My account": My account is a link
        # "Order history" is declared by REQ-2, which both pages hold: the page named like it gets it
        self.assertEqual([c["name"] for c in orders["controls"]], ["Order history"])
        self.assertNotIn("Order history", names)

    def test_the_plan_and_test_prompts_know_from(self):
        self.assertIn('"from X"', load_prompt("plan")); self.assertIn('"from X"', load_prompt("tests"))
        self.assertIn("X is a named region", load_prompt("plan"))

    # ---- a stated button rendered as a link -------------------------------------------------------------------------

    # Stack Overflow (v2 and v3 first versions): "Log in"(role=button) was a <Link> in the header; 41 to 48 of 66 tests
    # failed on it. The login form's own submit button "Log in" hid it from the old check.
    def test_a_stated_button_rendered_as_a_link_is_reported(self):
        plan = {"layout": {"controls": [{"role": "button", "name": "Log in", "to": "/login", "when": "signed_out"}]},
                "pages": [{"id": "login", "module": "m1", "controls": [{"role": "button", "name": "Log in", "when": "always"}]}]}
        page = 'export function PageLogin() { return <form noValidate><button type="submit">Log in</button></form>; }\n'
        shell = 'export function Layout() { return <header><Link to="/login">Log in</Link></header>; }\n'
        issue, = text_control_issues({"frontend/src/shared.jsx": shell, "frontend/src/pages/m1.jsx": page}, plan)
        self.assertTrue(issue.startswith('button "Log in" is rendered as a link (<Link>) in frontend/src/shared.jsx'))
        fixed = 'export function Layout() { return <header><button type="button" onClick={() => go("/login")}>Log in</button></header>; }\n'
        self.assertEqual(text_control_issues({"frontend/src/shared.jsx": fixed, "frontend/src/pages/m1.jsx": page}, plan), [])

    def test_an_unstated_action_in_a_link_is_not_judged(self):
        # Ctrip: "Actions described only as controls may be buttons or links"; the plan keeps a routed button only when
        # the requirements state the role, so a button without "to" may be either
        plan = {"layout": {"controls": [{"role": "button", "name": "Account security", "when": "always"}]}, "pages": []}
        shell = 'export function Layout() { return <nav><Link to="/security">Account security</Link></nav>; }\n'
        self.assertEqual(text_control_issues({"frontend/src/shared.jsx": shell}, plan), [])

    def test_the_shell_prompts_keep_the_planned_role(self):
        self.assertIn('role "button" is a <button type="button"> whose onClick calls navigate', load_prompt("frontend_first"))
        self.assertIn("A control with \"to\" keeps its planned role", load_prompt("frontend_more"))

    # ---- test files that do not load --------------------------------------------------------------------------------

    # 12306 (v3 graded 65/142): a duplicate const in one generated file; Playwright then runs no test at all, every
    # check reported 0 of 0 and 25 repair rounds ran blind.
    def test_the_files_named_by_load_errors_are_found(self):
        errors = ["SyntaxError: /workspace/template/.arc/harness/tests/scenarios-4.spec.ts: Identifier 'g2002Row' has "
                  "already been declared. (590:8)\n\n  588 |   await x;", "Error: No tests found"]
        self.assertEqual(unloadable_files(errors), ["scenarios-4.spec.ts"])

    def test_the_other_files_run_when_one_does_not_load(self):
        harness = self.tmp / "harness"; (harness / "tests").mkdir(parents=True)
        for name in ("scenarios-1.spec.ts", "scenarios-2.spec.ts", "smoke.spec.ts"):
            (harness / "tests" / name).write_text("// x\n")
        runs = []
        def fake_run(args, cwd, timeout, env=None):
            runs.append(args[3:])
            report = harness / "results.json"
            if len(runs) == 1:
                report.write_text(json.dumps(results([], [f"SyntaxError: {harness}/tests/scenarios-2.spec.ts: bad (3:1)"])))
            else:
                report.write_text(json.dumps(results(["scenarios-1.spec.ts"])))
            return ProcResult(0, "", 0.0)
        with mock.patch("arcflash.harness.proc.run", side_effect=fake_run):
            tests, load_errors, _ = run_playwright(harness, "http://x", harness / "smoke.json", timeout=60)
        self.assertEqual(runs[0], []); self.assertEqual(sorted(runs[1]), [r"scenarios\-1\.spec\.ts$", r"smoke\.spec\.ts$"])
        self.assertEqual([t.status for t in tests], ["passed"]); self.assertIn("scenarios-2.spec.ts", load_errors[0])

    def test_a_test_file_that_does_not_load_gets_a_small_fix(self):
        ws = Workspace(self.tmp / "out"); ws.init(FIXTURES / "specs" / "notes", [])
        broken = 'test("a", async () => {\n  const row = 1;\n  const row = 2;\n});\n'
        ws.write({"tests/scenarios-1.spec.ts": broken})
        error = f"SyntaxError: {ws.harness}/tests/scenarios-1.spec.ts: Identifier 'row' has already been declared. (3:8)"
        answer = ("=== EDIT: tests/scenarios-1.spec.ts ===\n<<<<<<< SEARCH\n  const row = 2;\n=======\n  const row2 = 2;\n"
                  ">>>>>>> REPLACE\n=== END EDIT ===\n")
        llm = FakeLLM({"quick_fix:tests": [answer]})
        with mock.patch("arcflash.repair.load_errors_of", return_value=[]) as listed:
            changed = fix_unloadable_tests(ws, llm, [error], "a1")
        self.assertIn("const row2 = 2;", changed["tests/scenarios-1.spec.ts"])
        self.assertIn("const row2 = 2;", (ws.harness / "tests" / "scenarios-1.spec.ts").read_text())
        listed.assert_called_once(); call, = llm.calls
        self.assertEqual((call["purpose"], call["max_tokens"]), ("quick_fix:tests.a1.scenarios-1.spec.ts.1", config.MAX_TOKENS["quick_fix"]))
        self.assertIn("   3 |   const row = 2;", call["user"]); self.assertIn("does not load", call["user"])

    # ---- when the repairs stop ---------------------------------------------------------------------------------------

    def report(self, passed, total=3, static=()):
        tests = [TestResult(f"SCN-{i} t", "scenarios-1.spec.ts", "passed" if i < passed else "failed",
                            error="" if i < passed else "Error: x", url="http://127.0.0.1:1/notes") for i in range(total)]
        return CheckReport(static_issues=list(static), built=True, booted=True, scenarios=tests,
                           smoke=[TestResult("smoke /", "smoke.spec.ts", "passed")], browser_checked=True)

    def workspace(self):
        ws = Workspace(self.tmp / "out"); ws.init(FIXTURES / "specs" / "notes", [])
        ws.reset_to_template(); ws.write(golden_files())
        return ws

    # Keep (v3 graded): 13 more rounds after 33/33 for one static issue, ¥3.76 against v2's ¥0.62.
    def test_all_scenarios_passing_ends_the_repairs(self):
        done = self.report(3, static=["frontend/src/pages/m1.jsx repeats the layout button \"Close\""])
        self.assertTrue(scenarios_done(done)); self.assertFalse(done.all_green())
        llm = FakeLLM({})
        for final in (False, True):
            best, snap = repair_loop(self.workspace(), "CTX", golden_plan(), llm, done, lambda: done, mock.Mock(allows=lambda k: True),
                                     "a1", final=final)
            self.assertEqual(snap.name, "a1-r0")
        self.assertEqual(llm.calls, [])

    def test_the_final_repairs_never_run_on_a_check_without_scenarios(self):
        blind = CheckReport(static_issues=["x"], built=True, booted=True, browser_checked=True,
                            smoke=[TestResult("smoke /", "smoke.spec.ts", "passed")])
        llm = FakeLLM({})
        repair_loop(self.workspace(), "CTX", golden_plan(), llm, blind, lambda: blind, mock.Mock(allows=lambda k: True), "a1c",
                    final=True)
        self.assertEqual(llm.calls, [])

    def test_the_final_repairs_go_on_after_rounds_without_gain(self):
        failing = self.report(1)
        allowed = iter([True] * 5 + [False] * 10)
        llm = FakeLLM({"repair:": ["no edits"] * 5})
        repair_loop(self.workspace(), "CTX", golden_plan(), llm, failing, lambda: failing,
                    mock.Mock(allows=lambda k: next(allowed)), "a1c", final=True)
        self.assertEqual(len(llm.calls), 5)   # not 3: three rounds without gain do not end the final repairs

    def test_the_final_repairs_end_after_six_rounds_in_a_row_without_gain(self):
        # BookStack lane: 14 final rounds at 32/35; ¥1 weighs as much as about 2.4 tests in the score
        failing = self.report(1)
        llm = FakeLLM({"repair:": ["no edits"] * 20})
        repair_loop(self.workspace(), "CTX", golden_plan(), llm, failing, lambda: failing, mock.Mock(allows=lambda k: True),
                    "a1c", final=True)
        self.assertEqual(len(llm.calls), config.MAX_FINAL_ROUNDS_WITHOUT_GAIN)
        self.assertEqual(config.MAX_FINAL_ROUNDS_WITHOUT_GAIN, 6)

    # ---- failure groups and message lookups --------------------------------------------------------------------------

    # 12306 (v2 graded): the booking step's failure was split into seven groups by the passenger names in its locator.
    def test_group_keys_ignore_per_test_record_names(self):
        a = ("TimeoutError: locator.selectOption: Timeout 10000ms exceeded.\n  - waiting for getByRole('row', { name: "
             "'Passenger Example [bf2899f29f7e57bb-w10-SCN-REQ-5-3-1-01-Open-unpaid-order-view]' })")
        b = a.replace("bf2899f29f7e57bb-w10-SCN-REQ-5-3-1-01-Open-unpaid-order-view", "4c14d211cafbf1db-w20-SCN-REQ-5-3-3-01-Resume")
        c = "Error: x\n  Locator: getByText('traveler_8e3f1a2b3cw4@example.com')"
        d = c.replace("8e3f1a2b3cw4", "0a1b2c3d4ew11")
        self.assertEqual(_group_key(a), _group_key(b)); self.assertEqual(_group_key(c), _group_key(d))

    # 12306 (v2): 53 of 109 first-check failures looked up unnamed messages by name, as status in one test and alert in
    # another; the repairs then rendered every success message twice (status and alert) to satisfy both.
    def test_named_statuses_are_looked_up_by_name_and_other_messages_by_text(self):
        write_named(self.tmp, [("status", "Note deleted"), ("region", "Feed"), ("alert", "Sign-in failed")])
        named = (self.tmp / "tests" / "named.ts").read_text()
        self.assertIn('{"status": ["Note deleted"], "alert": ["Sign-in failed"]}', named)
        helpers = (AGENT_ROOT / "arcflash" / "template" / "harness" / "tests" / "helpers.ts").read_text()
        self.assertIn('import { NAMED } from "./named";', helpers)
        self.assertIn('surface.getByRole("status").or(surface.getByRole("alert")).filter({ hasText: name })', helpers)
        self.assertTrue((AGENT_ROOT / "arcflash" / "template" / "harness" / "tests" / "named.ts").is_file())

    # ArcZenith Keep lane (2026-11-16): a white page reported only "the page rendered nothing"; the cause, "ReferenceError:
    # notification is not defined", came last in the smoke error and the repair prompt shows its first lines only.
    def test_the_page_check_reports_page_errors_first(self):
        smoke = (AGENT_ROOT / "arcflash" / "template" / "harness" / "tests" / "smoke.spec.ts").read_text()
        self.assertLess(smoke.index('expect.soft(errors, errors.join("\\n")).toEqual([]);'), smoke.index("the page rendered nothing"))

    def test_rule_8_gives_success_messages_status_and_a_name(self):
        rules = load_prompt("rules")
        self.assertIn('Errors use role="alert"; success and status messages use role="status"', rules)
        self.assertIn("otherwise to its own text", rules)


if __name__ == "__main__":
    unittest.main()
