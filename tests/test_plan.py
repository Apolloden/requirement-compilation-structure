import copy
import dataclasses
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from arcflash import config
from arcflash.llm import LLM
from arcflash.plan import PlanError, make_plan, page_component, settle_areas, settle_modules, settle_plan, validate_plan
from arcflash.prompting import build_context, load_prompt
from arcflash.spec import load_spec
from tests.fakes import FakeClient, FakeLLM, cut_chunks, stream_chunks
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
        plan["pages"].append({"id": f"extra_{i}", "route": f"/extra/{i}", "title": "Notes", "layout": True, "module": "m1",
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
             'page notes: control name "Add Note" is not in the requirements; check the wording'),
            ("twice on page", lambda p: page(p, "note_new")["controls"].append({"role": "button", "name": "Cancel", "when": "always"}),
             'page note_new: button "Cancel" appears twice'),
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
            ("too many pages", nine_pages, "module m1 has 9 pages; at most 6"),
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

    # Task 8: modules follow requirement groups (evidence.md 3.3: 12306's booking and payment pages were split apart).
    @staticmethod
    def grouped(*pages):
        """A plan whose pages are (id, reqs) in list order; the first page is the home page "/"."""
        plan = golden_plan()
        plan["pages"] = [{"id": pid, "route": "/" if n == 0 else f"/{pid}", "title": "x", "layout": True, "module": "any",
                          "reqs": reqs, "controls": [], "shows": [], "flows": []} for n, (pid, reqs) in enumerate(pages)]
        plan["modules"] = [{"id": "any", "pages": [pid for pid, _ in pages]}]
        settle_modules(plan)
        return [m["pages"] for m in plan["modules"]], {p["id"]: p["module"] for p in plan["pages"]}, [m["id"] for m in plan["modules"]]

    def test_pages_of_one_requirement_group_share_a_module(self):
        pages = [("home", ["REQ-1.1"])] + [(f"a{i}", ["REQ-2.1"]) for i in range(3)] + [(f"b{i}", ["REQ-3.1"]) for i in range(3)] \
            + [("pay", ["REQ-2.4.1", "REQ-3.1"])]   # most of its reqs: tie, the first listed (REQ-2) wins
        modules, owner, ids = self.grouped(*pages)
        self.assertEqual(modules, [["home"], ["a0", "a1", "a2", "pay"], ["b0", "b1", "b2"]])
        self.assertEqual((owner["pay"], owner["b0"], ids), ("m2", "m3", ["m1", "m2", "m3"]))

    def test_small_groups_join_the_previous_module(self):
        modules, _, _ = self.grouped(("home", ["REQ-1.1"]), ("a0", ["REQ-2.1"]), ("a1", ["REQ-2.2"]), ("a2", ["REQ-2.3"]),
                                     ("b0", ["REQ-3.1"]), ("c0", ["REQ-4.1"]), ("c1", ["REQ-4.1"]), ("c2", ["REQ-4.1"]))
        # home (1 page) has no module before it; b0 (1 page) joins a*; c* (3 pages) stays
        self.assertEqual(modules, [["home"], ["a0", "a1", "a2", "b0"], ["c0", "c1", "c2"]])
        self.assertEqual(config.MAX_PAGES_PER_MODULE, 6)

    def test_a_group_over_the_limit_is_split_in_list_order(self):
        modules, owner, _ = self.grouped(("home", ["REQ-1.1"]), *[(f"a{i}", ["REQ-2.1"]) for i in range(9)])
        self.assertEqual(modules, [["home"], [f"a{i}" for i in range(6)], ["a6", "a7", "a8"]])
        self.assertEqual(owner["a6"], "m3")

    def test_the_home_page_is_in_the_first_module(self):
        plan = golden_plan()
        plan["pages"] = [{"id": "a", "route": "/a", "layout": True, "module": "x", "reqs": ["REQ-2.1"], "controls": []},
                         {"id": "home", "route": "/", "layout": True, "module": "x", "reqs": ["REQ-1.1"], "controls": []},
                         {"id": "b", "route": "/b", "layout": True, "module": "x", "reqs": ["REQ-1.2"], "controls": []}]
        settle_modules(plan)
        self.assertEqual(plan["modules"], [{"id": "m1", "pages": ["home", "b", "a"]}])   # "a" (1 page) joins the module before
        self.assertEqual(next(p for p in plan["pages"] if p["id"] == "home")["module"], "m1")

    def test_a_page_without_reqs_follows_the_page_before(self):
        _, owner, _ = self.grouped(("home", ["REQ-1.1"]), ("a0", ["REQ-2.1"]), ("a1", ["REQ-2.1"]), ("a2", []),
                                   ("b0", ["REQ-3.1"]), ("b1", ["REQ-3.1"]), ("b2", ["REQ-3.1"]))
        self.assertEqual(owner["a2"], owner["a0"])

    # Task 7: the plan is settled by code; the plan-fix calls took 102 of 604 minutes in v4 (evidence.md 2.4).
    def test_one_plan_call_and_no_plan_fix(self):
        llm = FakeLLM({"plan": [json.dumps(mutated(without_req_1_3))]})
        plan, notes = make_plan(load_spec(NOTES), "", llm, temperature=0.6)
        self.assertEqual(([c["purpose"] for c in llm.calls], notes), (["plan"], []))
        self.assertIn("REQ-1.3", page(plan, "notes")["reqs"])
        self.assertEqual(llm.calls[0]["temperature"], 0.6)

    def test_an_unparsable_plan_gets_one_retry_with_the_error(self):
        llm = FakeLLM({"plan-retry": [json.dumps(golden_plan())], "plan": ["not json"]})
        plan, notes = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual(([c["purpose"] for c in llm.calls], notes), (["plan", "plan-retry"], []))
        self.assertTrue(llm.calls[1]["user"].startswith(llm.calls[0]["user"] + "\n\nYour previous answer could not be read as one JSON object: "))
        shape = FakeLLM({"plan-retry": [json.dumps(golden_plan())], "plan": [json.dumps({"app": [], "pages": {}})]})
        self.assertEqual(make_plan(load_spec(NOTES), "", shape, temperature=0.2)[0], golden_plan())
        self.assertIn("plan.app must be an object", shape.calls[1]["user"])

    def test_plan_notes_reach_the_code_calls_inside_the_plan(self):
        llm = FakeLLM({"plan": [json.dumps(mutated(lambda p: page(p, "notes")["controls"][1].update(name="Add Note")))]})
        plan, notes = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual(notes, ['page notes: control name "Add Note" is not in the requirements; check the wording'])
        self.assertEqual(plan["plan_notes"], notes)

    def test_never_parsed_raises(self):
        llm = FakeLLM({"plan-retry": ["[1, 2]"], "plan": ["not json"]})
        with self.assertRaises(PlanError): make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual(len(llm.calls), 2)

    def settled(self, change, spec=None):
        plan = mutated(change)
        return plan, settle_plan(plan, spec or self.spec)

    def test_runtime_names_move_to_shows(self):
        plan, notes = self.settled(lambda p: page(p, "note")["controls"] + [
            page(p, "note")["controls"].append({"role": "heading", "name": "{note title}", "when": "always"}),
            page(p, "note")["controls"].append({"role": "region", "name": "Note <created title>", "when": "always"})])
        names = [c["name"] for c in page(plan, "note")["controls"]]
        self.assertNotIn("{note title}", names); self.assertNotIn("Note <created title>", names)
        self.assertIn('heading "{note title}"', page(plan, "note")["shows"])
        self.assertIn('region "Note <created title>"', page(plan, "note")["shows"])
        self.assertEqual(notes, [])

    def test_a_template_name_from_the_requirements_is_still_a_runtime_name(self):
        # v4 Keep: the spec writes link names as "Open {title}"; 17 such controls were sent to plan-fix.
        spec = dataclasses.replace(self.spec, corpus=self.spec.corpus + '\nEach note has a link named "Open {title}".')
        plan, notes = self.settled(lambda p: page(p, "notes")["controls"].append({"role": "link", "name": "Open {title}", "when": "per note"}), spec)
        self.assertNotIn("Open {title}", [c["name"] for c in page(plan, "notes")["controls"]])
        self.assertIn('link "Open {title}"', page(plan, "notes")["shows"]); self.assertEqual(notes, [])

    def test_a_bracketed_name_from_the_requirements_stays_a_control(self):
        spec = dataclasses.replace(self.spec, corpus=self.spec.corpus + '\n"Tag <draft>"(role=button) marks a draft.',
                                   role_names=self.spec.role_names + [("button", "Tag <draft>")])
        plan, notes = self.settled(lambda p: page(p, "note")["controls"].append({"role": "button", "name": "Tag <draft>", "when": "always"}), spec)
        self.assertIn("Tag <draft>", [c["name"] for c in page(plan, "note")["controls"]])
        self.assertEqual(notes, [])

    def test_exact_duplicates_go_and_different_when_stays(self):
        def change(p):
            c = page(p, "note_new")["controls"]
            c.append({"role": "button", "name": "Cancel", "when": "always"})          # exact duplicate
            page(p, "note")["controls"].append({"role": "button", "name": "Delete", "when": "per row"})
        plan, notes = self.settled(change)
        self.assertEqual([c["name"] for c in page(plan, "note_new")["controls"]].count("Cancel"), 1)
        self.assertEqual([c["name"] for c in page(plan, "note")["controls"]].count("Delete"), 2)
        self.assertEqual(notes, [])

    def test_a_layout_repeat_becomes_a_note_for_the_code_writers(self):
        # evidence.md 6.4: the grader does not scope an unscoped lookup, so a page control with a layout name must
        # either be scoped by the requirement or hide the layout's control on that page.
        plan, notes = self.settled(lambda p: page(p, "notes")["controls"].append({"role": "link", "name": "Notes", "when": "always"}))
        self.assertIn("Notes", [c["name"] for c in page(plan, "notes")["controls"]])
        self.assertEqual(notes, ['page notes shows link "Notes" like the layout; unless the requirement acts on it inside a named '
                                 'form or region, hide the layout\'s "Notes" on route /notes'])

    def test_an_orphan_requirement_joins_the_page_of_its_group(self):
        plan, notes = self.settled(without_req_1_3)
        self.assertEqual([p["id"] for p in plan["pages"] if "REQ-1.3" in p["reqs"]], ["notes"])   # most REQ-1.* reqs
        self.assertEqual(notes, [])

    def test_a_named_control_on_no_page_is_added_to_its_requirement_page(self):
        plan, notes = self.settled(lambda p: page(p, "notes")["controls"].pop(0))   # region "Note list" (REQ-1.1)
        self.assertIn({"role": "region", "name": "Note list", "when": "always"}, page(plan, "notes")["controls"])
        self.assertEqual(notes, [])

    def test_ids_routes_api_and_roles_are_fixed_mechanically(self):
        def change(p):
            page(p, "notes").update(route="notes")
            p["api"][0]["method"] = "get"; p["api"].append(dict(p["api"][0]))
            p.update(auth={"accounts": True})
            page(p, "notes")["controls"].append({"role": "menuitem", "name": "Create Note", "when": "always", "to": "/notes/new"})
        plan, notes = self.settled(change)
        self.assertEqual(page(plan, "notes")["route"], "/notes")
        self.assertEqual([(a["method"], a["path"]) for a in plan["api"]].count(("GET", "/api/notes")), 1)
        self.assertIn(("GET", "/api/session"), [(a["method"], a["path"]) for a in plan["api"]])
        self.assertNotIn("menuitem", [c["role"] for c in page(plan, "notes")["controls"]])
        self.assertEqual(notes, [])
        upper, notes = self.settled(lambda p: p["pages"][0].update(id="Home"))
        self.assertEqual((upper["pages"][0]["id"], notes), ("home", []))

    def test_an_unknown_role_stays_with_a_note(self):
        plan, notes = self.settled(lambda p: page(p, "notes")["controls"].append({"role": "menu-item", "name": "Notes", "when": "after x"}))
        self.assertIn('page notes: role "menu-item" is not an ARIA role', notes)

    def test_fenced_json_is_read(self):
        llm = FakeLLM({"plan": ["```json\n" + json.dumps(golden_plan()) + "\n```"]})
        self.assertEqual(make_plan(load_spec(NOTES), "", llm, temperature=0.2), (golden_plan(), []))

    def test_plan_answer_cut_by_length_is_continued(self):
        text = json.dumps(golden_plan()); cut = len(text) // 2
        llm = LLM(client=FakeClient([stream_chunks(text[:cut], finish="length"), stream_chunks(text[cut:])]), sleep=lambda s: None)
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual((plan, problems), (golden_plan(), []))

    def test_a_cut_plan_that_parses_is_not_continued(self):
        llm = LLM(client=FakeClient([cut_chunks(json.dumps(golden_plan())), stream_chunks("}")]), sleep=lambda s: None)
        plan, problems = make_plan(load_spec(NOTES), "", llm, temperature=0.2)
        self.assertEqual((plan, problems, len(llm.client.requests)), (golden_plan(), [], 1))

    def test_a_cut_plan_that_does_not_parse_is_continued(self):
        text = json.dumps(golden_plan()); cut = len(text) // 2
        llm = LLM(client=FakeClient([cut_chunks(text[:cut]), stream_chunks(text[cut:])]), sleep=lambda s: None)
        self.assertEqual(make_plan(load_spec(NOTES), "", llm, temperature=0.2), (golden_plan(), []))

    def test_an_unknown_area_is_dropped_from_the_plan(self):
        # Task 6b: "in" names an area of the same page or of the layout; anything else is dropped and noted.
        plan = golden_plan()
        controls = next(p for p in plan["pages"] if p["id"] == "notes")["controls"]
        controls[1]["in"] = "Note list"
        controls.append({"role": "button", "name": "Refresh", "when": "always", "in": "Sidebar"})
        notes = settle_areas(plan)
        self.assertEqual(controls[1]["in"], "Note list"); self.assertNotIn("in", controls[2])
        self.assertEqual(notes, ['page notes: button "Refresh" was placed in "Sidebar", which is no area of that page or the layout'])

    def test_page_component(self):
        self.assertEqual((page_component("item_detail"), page_component("home")), ("PageItemDetail", "PageHome"))


if __name__ == "__main__":
    unittest.main()
