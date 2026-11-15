"""ArcLaser v2: the fixes found in the graded ArcLaser run of 2026-11-15 (docs/7-arclaser-v2/README.md)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from arcflash import config
from arcflash.check_static import area_issues, seed_issues
from arcflash.llm import complete_lines
from arcflash.plan import infer_areas, settle_plan, settle_shared_navs
from arcflash.spec import load_spec

SPEC = '''id: ROOT
name: Shop
description:
  text: A small shop.
children:
- id: REQ-1
  name: Account
  description:
    text: |-
      "My account"(role=button) opens an account menu with Orders. "Product feed"(role=region) displays Ask Question.
  scenarios:
  - id: SCN-REQ-1-01
    name: Open orders
    steps:
    - id: STEP-1
      Given: |-
        buyer has signed in.
        Seed prerequisites (prepared before this scenario):
        - verified accounts: {"buyer": {"email": "buyer@example.com", "password": "Password123!", "username": "buyer_one"}}
        - orders: [{"number": "ORD20260001", "total": 5}]
      When: Click My account, then activate Orders. Click Ask Question in Product feed.
      Then: Orders heading opens.
  - id: SCN-REQ-1-02
    name: Again
    steps:
    - id: STEP-2
      Given: |-
        Seed prerequisites (prepared before this scenario):
        - verified accounts: {"buyer": {"email": "buyer@example.com", "password": "Password123!", "username": "buyer_one"}}
      When: Open the shop.
      Then: The shop opens.
  children: []
'''


def plan_for(**extra):
    plan = {"app": {"title": "Shop"}, "auth": {"accounts": False}, "tables": [], "api": [],
            "layout": {"controls": [{"role": "button", "name": "My account", "when": "always"},
                                    {"role": "button", "name": "Orders", "to": "/orders", "when": "after My account"}]},
            "pages": [{"id": "home", "route": "/", "layout": True, "reqs": ["REQ-1"],
                       "controls": [{"role": "region", "name": "Product feed", "when": "always"},
                                    {"role": "button", "name": "Ask Question", "when": "always"},
                                    {"role": "heading", "name": "Ask Question", "when": "always"}]},
                      {"id": "orders", "route": "/orders", "layout": True, "reqs": [], "controls": []}]}
    plan.update(extra)
    return plan


class ArcLaserV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "requirements.yaml").write_text(SPEC, encoding="utf-8")
        self.spec = load_spec(self.tmp)

    # Stack Overflow: the plan's copy of the seed data dropped 18 of 24 accounts; every scenario using them failed.
    def test_seed_lines_and_values_come_from_the_scenarios(self):
        self.assertEqual(len(self.spec.seed_lines), 2)   # the repeated account line once
        self.assertEqual(self.spec.seed_values, ["buyer@example.com", "buyer_one", "ORD20260001"])

    def test_a_seed_value_missing_from_the_backend_is_reported(self):
        plan = {"seed_values": self.spec.seed_values}
        files = {"backend/app.js": 'seed("buyer@example.com", "buyer_one");'}
        issue, = seed_issues(files, plan)
        self.assertIn('backend/app.js seeds no record with "ORD20260001"', issue); self.assertNotIn("buyer_one", issue)
        files["backend/app.js"] += ' order("ORD20260001");'
        self.assertEqual(seed_issues(files, plan), [])

    def test_the_backend_gets_the_seed_list_and_the_plan_drops_seed(self):
        from arcflash.prompting import load_prompt
        self.assertIn("{{seeds}}", load_prompt("backend")); self.assertNotIn('"seed":', load_prompt("plan"))

    # Ctrip: the account menu's Orders became a button with a route; the grader looked for a link (v4's link passed).
    def test_a_button_that_opens_a_page_is_a_link_unless_the_button_role_is_written(self):
        plan = plan_for()
        settle_plan(plan, self.spec)
        roles = {c["name"]: c["role"] for c in plan["layout"]["controls"]}
        self.assertEqual(roles, {"My account": "button", "Orders": "link"})

    # Stack Overflow: "Click Ask Question in Product feed" was not planned inside the feed.
    def test_a_control_written_as_in_an_area_is_placed_there(self):
        plan = plan_for()
        infer_areas(plan, self.spec)
        controls = plan["pages"][0]["controls"]
        self.assertEqual(controls[1].get("in"), "Product feed")
        self.assertNotIn("in", controls[2])   # a heading is not an action
        self.assertNotIn("in", plan["layout"]["controls"][1])   # a layout control never goes into a page's area

    def test_a_component_that_takes_the_area_name_is_an_area(self):
        plan = {"layout": {"controls": []}, "pages": [{"id": "home", "controls": [
            {"role": "region", "name": "Product feed"}, {"role": "button", "name": "Ask Question", "in": "Product feed"}]}]}
        outside = '<Region name="Product feed"><p>x</p></Region>\n<button>Ask Question</button>\n'
        inside = '<Region name="Product feed"><button>Ask Question</button></Region>\n'
        comment = '// usage: <Region name="Product feed">...</Region>\nfunction A() { return <button>Ask Question</button>; }\n'
        self.assertEqual(len(area_issues({"frontend/src/pages/m1.jsx": outside}, plan)), 1)
        self.assertEqual(area_issues({"frontend/src/pages/m1.jsx": inside}, plan), [])
        self.assertEqual(area_issues({"frontend/src/pages/m1.jsx": comment}, plan), [])

    # 12306: three modules each wrote their own "Personal center menu"; one lacked My Passengers.
    def test_a_navigation_on_pages_of_several_modules_is_shared(self):
        nav = {"role": "navigation", "name": "Side menu", "when": "always"}
        link = lambda name: {"role": "link", "name": name, "to": "/" + name.lower(), "in": "Side menu", "when": "always"}
        plan = {"pages": [{"id": "a", "module": "m1", "controls": [nav, link("Info")]},
                          {"id": "b", "module": "m2", "controls": [nav, link("Info"), link("Passengers")]},
                          {"id": "c", "module": "m2", "controls": []}]}
        settle_shared_navs(plan)
        shared, = plan["shared_navs"]
        self.assertEqual((shared["component"], shared["pages"]), ("NavSideMenu", ["a", "b"]))
        self.assertEqual([c["name"] for c in shared["controls"]], ["Info", "Passengers"])
        plan["pages"][1]["module"] = "m1"
        settle_shared_navs(plan); self.assertNotIn("shared_navs", plan)

    # 12306: the plan broke at a continuation seam ("in": " + "Filter") after 68 minutes.
    def test_a_cut_answer_is_continued_from_its_last_complete_line(self):
        self.assertEqual(complete_lines('{"a": 1},\n{"in": "'), '{"a": 1},\n')
        self.assertEqual(complete_lines("whole line\n"), "whole line\n")
        one_line = "x" * (config.CONTINUE_LINE_CHARS + 10)
        self.assertEqual(complete_lines("start\n" + one_line), "start\n" + one_line)   # minified JSON: continued as it is


if __name__ == "__main__":
    unittest.main()
