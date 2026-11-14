import copy
import dataclasses
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from arcflash import config
from arcflash.llm import LLM
from arcflash.plan import PlanError, make_plan, page_component, validate_plan
from arcflash.prompting import build_context, load_prompt
from arcflash.spec import load_spec
from tests.fakes import FakeClient, FakeLLM, stream_chunks
from tests.golden import golden_plan
from tests.support import FIXTURES

NOTES = FIXTURES / "specs" / "notes"


def mutated(change):
    plan = copy.deepcopy(golden_plan())
    change(plan)
    return plan


def page(plan, page_id):
    return next(p for p in plan["pages"] if p["id"] == page_id)


def without_req_1_3(plan):
    for p in plan["pages"]:
        p["reqs"] = [r for r in p["reqs"] if r != "REQ-1.3"]


def nine_pages(plan):
    for i in range(5):
        plan["pages"].append({"id": f"extra_{i}", "route": f"/extra/{i}", "title": "Notes", "layout": True, "module": "main",
                              "reqs": [], "controls": [], "shows": [], "flows": []})
        plan["modules"][0]["pages"].append(f"extra_{i}")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec(NOTES)

    def test_golden_plan_is_valid(self):
        self.assertEqual(validate_plan(golden_plan(), self.spec), [])

    def test_shape_problems_come_alone(self):
        self.assertEqual(validate_plan({"app": [], "layout": {}, "tables": [], "api": [], "pages": [], "modules": []}, self.spec),
                         ["plan.app must be an object", "plan.layout.controls must be a list"])
        self.assertIn("plan.pages must be a list", validate_plan(mutated(lambda p: p.update(pages={})), self.spec))

    def test_each_rule_reports_its_problem(self):
        cases = [
            ("id", lambda p: p["pages"][0].update(id="Home"), "page 1 needs an id in lowercase snake_case"),
            ("id twice", lambda p: p["pages"][1].update(id="home"), 'pages: id "home" is used twice'),
            ("route slash", lambda p: page(p, "notes").update(route="notes"), 'page notes: route must start with "/"'),
            ("route twice", lambda p: p["pages"][2].update(route="/notes"), 'pages: route "/notes" is used twice'),
            ("unknown req", lambda p: page(p, "notes")["reqs"].append("REQ-9"), "page notes: unknown requirement REQ-9"),
            ("req on no page", without_req_1_3, "requirement REQ-1.3 is on no page"),
            ("role", lambda p: page(p, "notes")["controls"].append({"role": "menu-item", "name": "Notes", "when": "always"}),
             'page notes: role "menu-item" is not an ARIA role'),
            ("layout role", lambda p: p["layout"]["controls"][0].update(role="nav-link"), 'layout: role "nav-link" is not an ARIA role'),
            ("runtime name", lambda p: page(p, "note")["controls"].append({"role": "heading", "name": "{note title}", "when": "always"}),
             'page note: control name "{note title}" is filled in at runtime; describe it in "shows"'),
            ("name not in text", lambda p: page(p, "notes")["controls"][1].update(name="Add Note"),
             'page notes: control name "Add Note" is not in the requirements or screenshot notes; copy names exactly'),
            ("twice on page", lambda p: page(p, "note_new")["controls"].append({"role": "button", "name": "Cancel", "when": "always"}),
             'page note_new: button "Cancel" appears twice'),
            ("repeats layout", lambda p: page(p, "notes")["controls"].append({"role": "link", "name": "Notes", "when": "always"}),
             'page notes: link "Notes" repeats a layout control; header controls belong only in layout'),
            ("role name missing", lambda p: page(p, "notes")["controls"].pop(0),
             'the requirements name region "Note list", but no page or layout control has it'),
            ("api method", lambda p: p["api"][0].update(method="FETCH"), "api 1: method must be GET, POST, PUT, PATCH or DELETE"),
            ("api path", lambda p: p["api"][0].update(path="/notes"), "api GET /notes: path must start with /api/"),
            ("api twice", lambda p: p["api"].append(dict(p["api"][0])), "api: GET /api/notes is listed twice"),
            ("auth", lambda p: p.update(auth={"accounts": True}), "auth.accounts is true but api has no GET /api/session"),
            ("module id", lambda p: (p["modules"][0].update(id="Main"), [q.update(module="Main") for q in p["pages"]]),
             'module id "Main" must be lowercase snake_case'),
            ("module missing", lambda p: page(p, "note").update(module="other"), 'page note: module "other" does not exist'),
            ("in no module", lambda p: p["modules"][0]["pages"].remove("note"), "modules: page note is in no module"),
            ("in two modules", lambda p: p["modules"].append({"id": "more", "pages": ["note"]}), "modules: page note is in more than one module"),
            ("too many pages", nine_pages, "module main has 9 pages; at most 8"),
            ("table twice", lambda p: p["tables"].append(dict(p["tables"][0])), 'tables: name "notes" is used twice'),
            ("no columns", lambda p: p["tables"][0].update(columns=[]), "table notes needs at least one column"),
        ]
        for name, change, expected in cases:
            with self.subTest(name):
                self.assertIn(expected, validate_plan(mutated(change), self.spec))

    def test_role_only_element_has_an_empty_name(self):
        # P2: "display an error with role=alert" gives no name; invented names cost two fix calls.
        plan = mutated(lambda p: page(p, "note_new")["controls"].append({"role": "alert", "name": "", "when": "after a failed save"}))
        self.assertEqual(validate_plan(plan, self.spec), [])
        self.assertIn('"name": ""', load_prompt("plan"))

    def test_unstated_interactive_role(self):
        # evidence.md C2: the plan typed account-menu entries as menuitem although the spec gave them no role, so a
        # button-or-link lookup never found them.
        problem = 'page notes: menuitem "Notes" has a role the requirements do not state; an action they name without a role is a link or a button'
        plan = mutated(lambda p: page(p, "notes")["controls"].append({"role": "menuitem", "name": "Notes", "when": "always"}))
        self.assertIn(problem, validate_plan(plan, self.spec))
        stated = dataclasses.replace(self.spec, role_names=self.spec.role_names + [("menuitem", "Notes")])
        self.assertNotIn(problem, validate_plan(plan, stated))
        tab = mutated(lambda p: page(p, "notes")["controls"].append({"role": "tab", "name": "Note list", "when": "always"}))
        self.assertIn('page notes: tab "Note list" has a role the requirements do not state; an action they name without a role '
                      'is a link or a button', validate_plan(tab, self.spec))

    def test_role_stated_in_prose_counts(self):
        # Seen in the graded Ctrip spec: "reveals the Log out menuitem", "activate Log out (menuitem)", "the GHIJ tab",
        # "Pending, In progress, Completed lifecycle tabs". The same sentence names the control and its role.
        for sentence in ("Hovering the header reveals the Save Note menuitem.", "Then activate Save Note (menuitem).",
                         "Save Note, Cancel and Delete are menuitems."):
            with self.subTest(sentence):
                spec = dataclasses.replace(self.spec, corpus=self.spec.corpus + "\n" + sentence)
                plan = mutated(lambda p: page(p, "note_new")["controls"].append({"role": "menuitem", "name": "Save Note", "when": "always"}))
                self.assertEqual([x for x in validate_plan(plan, spec) if "has a role" in x], [])
        # Review finding 2: the role in a list heading on the line before, or in the sentence before.
        for sentence in ("The header menu has these menuitems:\n- Save Note\n- Cancel", "The form has two menuitems. They are Save Note and Cancel."):
            with self.subTest(sentence):
                spec = dataclasses.replace(self.spec, corpus=self.spec.corpus + "\n" + sentence)
                plan = mutated(lambda p: page(p, "note_new")["controls"].append({"role": "menuitem", "name": "Save Note", "when": "always"}))
                self.assertEqual([x for x in validate_plan(plan, spec) if "has a role" in x], [])
        other = dataclasses.replace(self.spec, corpus=self.spec.corpus + "\nSave Note is a button. The header has a menuitem.")
        plan = mutated(lambda p: page(p, "note_new")["controls"].append({"role": "menuitem", "name": "Save Note", "when": "always"}))
        self.assertEqual(len([x for x in validate_plan(plan, other) if "has a role" in x]), 1)

    def test_names_from_screenshot_notes_are_accepted(self):
        plan = mutated(lambda p: page(p, "notes")["controls"].append({"role": "button", "name": "Sort by date", "when": "always"}))
        self.assertTrue(validate_plan(plan, self.spec))
        self.assertEqual(validate_plan(plan, self.spec, 'Labels: "Sort by date"'), [])

    def test_unicode_names_validate(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(NOTES, tmp / "task")
        yaml_path = tmp / "task" / "requirements.yaml"
        yaml_path.write_text(yaml_path.read_text().replace("Manage notes.", 'Manage notes. "下一步"(role=button) moves on.'))
        spec = load_spec(tmp / "task")
        plan = mutated(lambda p: page(p, "notes")["controls"].append({"role": "button", "name": "下一步", "when": "always"}))
        self.assertEqual(validate_plan(plan, spec), [])
        self.assertIn('the requirements name button "下一步", but no page or layout control has it', validate_plan(golden_plan(), spec))

    def test_first_valid_answer_is_accepted(self):
        llm = FakeLLM({"plan": [json.dumps(golden_plan())]})
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual((plan, problems, len(llm.calls)), (golden_plan(), [], 1))
        self.assertTrue(llm.calls[0]["json_mode"]); self.assertEqual(llm.calls[0]["system"], load_prompt("rules"))
        self.assertEqual(llm.calls[0]["user"], build_context(self.spec, "") + "\n\n" + load_prompt("plan"))
        self.assertEqual((llm.calls[0]["method"], llm.calls[0]["max_tokens"], llm.calls[0]["temperature"]),
                         ("complete_long", config.MAX_TOKENS["plan"], 0.2))

    def test_module_membership_is_settled_by_code(self):
        # v3 Ctrip: 21 pages said "module": "main" while the module list put them elsewhere (plan.md R6).
        def moved(p):
            p["modules"][0]["pages"].remove("note")
            p["modules"].append({"id": "more", "pages": ["note"]})   # the list says "more", the page still says "main"
        def unlisted(p):
            p["modules"][0]["pages"].remove("note")                    # in no list; the page names "main"
        for name, change, module, listed in (("list wins", moved, "more", ["note"]), ("joins its module", unlisted, "main", None)):
            with self.subTest(name):
                llm = FakeLLM({"plan": [json.dumps(mutated(change))]})
                plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
                self.assertEqual((problems, len(llm.calls)), ([], 1))
                self.assertEqual(page(plan, "note")["module"], module)
                self.assertIn("note", next(m for m in plan["modules"] if m["id"] == module)["pages"])

    def test_problems_are_sent_back(self):
        plan_without_req_1_3 = mutated(without_req_1_3)
        llm = FakeLLM({"plan-fix": [json.dumps(golden_plan())], "plan": [json.dumps(plan_without_req_1_3)]})
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual(problems, []); self.assertEqual(llm.calls[1]["purpose"], "plan-fix-1")
        self.assertIn("- requirement REQ-1.3 is on no page", llm.calls[1]["user"])
        self.assertIn(json.dumps(plan_without_req_1_3), llm.calls[1]["user"])
        self.assertTrue(llm.calls[1]["user"].startswith(llm.calls[0]["user"] + "\n\nYour previous plan has these problems:\n"))

    def test_three_bad_answers_return_the_last_with_problems(self):
        bad = [json.dumps(mutated(without_req_1_3))] * 2
        last = mutated(lambda p: (without_req_1_3(p), p["pages"][0].update(title="Last")))
        llm = FakeLLM({"plan-fix": bad[:1] + [json.dumps(last)], "plan": bad[1:]})
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.6)
        self.assertEqual((plan["pages"][0]["title"], problems, len(llm.calls)), ("Last", ["requirement REQ-1.3 is on no page"], 3))
        self.assertEqual([c["purpose"] for c in llm.calls], ["plan", "plan-fix-1", "plan-fix-2"])
        self.assertTrue(all(c["temperature"] == 0.6 for c in llm.calls))

    def test_invalid_json_counts_as_an_attempt(self):
        llm = FakeLLM({"plan-fix": [json.dumps(golden_plan())], "plan": ["not json"]})
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual((len(llm.calls), problems), (2, []))
        self.assertIn("- the answer is not one valid JSON object:", llm.calls[1]["user"])

    def test_fenced_json_is_read(self):
        llm = FakeLLM({"plan": ["```json\n" + json.dumps(golden_plan()) + "\n```"]})
        self.assertEqual(make_plan(load_spec(NOTES), "", llm, temperature=0.2), (golden_plan(), []))

    def test_never_parsed_raises(self):
        llm = FakeLLM({"plan-fix": ["not json", "[1, 2]"], "plan": ["not json"]})
        with self.assertRaises(PlanError): make_plan(load_spec(NOTES), "", llm, temperature=0.2)

    def test_plan_answer_cut_by_length_is_continued(self):
        text = json.dumps(golden_plan()); cut = len(text) // 2
        llm = LLM(client=FakeClient([stream_chunks(text[:cut], finish="length"), stream_chunks(text[cut:])]), sleep=lambda s: None)
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual((plan, problems), (golden_plan(), []))

    def test_page_component(self):
        self.assertEqual((page_component("item_detail"), page_component("home")), ("PageItemDetail", "PageHome"))


if __name__ == "__main__":
    unittest.main()
