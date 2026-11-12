import unittest
from unittest.mock import Mock
from infrastructure.stages.design import planner
from design_fixtures import ReplayModel, answer, lite_contexts, lite_images, lite_tree

class DataPlan(unittest.TestCase):
    def test_real_answer_is_accepted_in_one_call(self):
        model = ReplayModel([answer("p1")])
        plan = planner.plan_data(lite_tree(), model, Mock())
        self.assertEqual(model.calls, 1)
        self.assertEqual(set(plan["entities"]), {"Book", "Page"})
        self.assertEqual(plan["apis"]["API-create-book"]["signature"]["method"], "POST")
        self.assertEqual(plan["conventions"], {})
        request = model.requests[0][1]
        self.assertEqual([r["id"] for r in request["requirements"]], lite_tree().leaves)
        self.assertEqual(request["global_rules"][0]["id"], "ROOT")

    def test_duplicate_route_is_repaired_on_the_second_attempt(self):
        bad = answer("p1"); bad["apis"]["API-copy"] = bad["apis"]["API-get-book"]
        model = ReplayModel([bad, answer("p1")])
        planner.plan_data(lite_tree(), model, Mock())
        self.assertEqual(model.calls, 2)
        self.assertIn("same method and path", model.requests[1][1]["correction"]["error"])

    def test_entity_without_primary_key_is_rejected(self):
        bad = answer("p1")
        for field in bad["entities"]["Book"]["fields"].values(): field["primary_key"] = False
        model = ReplayModel([bad, bad, bad])
        with self.assertRaisesRegex(RuntimeError, "exhausted 3 proposals"):
            planner.plan_data(lite_tree(), model, Mock())
        self.assertIn("Book has no primary key", model.requests[1][1]["correction"]["error"])

HEADER = {"UI-layout-header": {"type": "container", "parent": None, "mode": [], "data": [], "prop": {}},
          "UI-layout-nav-home": {"type": "link", "parent": "UI-layout-header", "mode": ["user_read"], "data": [],
                                 "prop": {"label": "Home", "href": "/"}}}

class UiPlan(unittest.TestCase):
    def plan(self, answers):
        tree = lite_tree()
        model = ReplayModel(answers)
        return planner.plan_ui(tree, lite_contexts(tree), lite_images(), answer("p1"), model, Mock()), model

    def test_real_answer_is_accepted(self):
        plan, model = self.plan([answer("p2")])
        self.assertEqual(model.calls, 1)
        self.assertEqual(plan["pages"]["book"]["route"], "/books/:id")

    def test_header_controls_are_added_to_the_naming_table(self):
        with_header = answer("p2"); with_header["layout"]["elements"] = HEADER
        for page in with_header["pages"].values(): page["layout"] = True
        plan, _ = self.plan([with_header])
        entry = next(e for e in plan["naming"] if e["name"] == "Home")
        self.assertEqual((entry["role"], sorted(entry["pages"])), ("link", sorted(plan["pages"])))

    def test_header_control_that_repeats_a_page_control_is_rejected(self):
        bad = answer("p2"); bad["layout"]["elements"] = {**HEADER, "UI-layout-nav-books": {"type": "link",
            "parent": "UI-layout-header", "mode": [], "data": [], "prop": {"label": "Books", "href": "/books"}}}
        for page in bad["pages"].values(): page["layout"] = True
        plan, model = self.plan([bad, answer("p2")])
        self.assertEqual(model.calls, 2)
        self.assertIn("'Books'", model.requests[1][1]["correction"]["error"])

    def test_leaf_without_a_page_and_duplicate_route_are_rejected(self):
        bad = answer("p2")
        for page in bad["pages"].values(): page["requirements"] = [r for r in page["requirements"] if r != "REQ-3.2"]
        bad["pages"]["copy"] = {**bad["pages"]["home"]}
        _, model = self.plan([bad, answer("p2")])
        error = model.requests[1][1]["correction"]["error"]
        self.assertIn("REQ-3.2", error); self.assertIn("same route", error)

    def test_skeleton_copies_the_header_into_every_header_page(self):
        with_header = answer("p2"); with_header["layout"]["elements"] = HEADER
        with_header["pages"]["home"]["layout"] = True
        design = planner.ui_skeleton(with_header, lite_tree())
        self.assertEqual(design["pages"]["home"]["root"], "UI-home")
        self.assertEqual(design["elements"]["UI-home-layout-header"]["parent"], "UI-home")
        self.assertEqual(design["elements"]["UI-home-layout-nav-home"]["parent"], "UI-home-layout-header")
        self.assertTrue(design["elements"]["UI-home-layout-nav-home"]["prop"]["layout"])
        self.assertNotIn("UI-books-layout-header", design["elements"])           # books has layout false
        self.assertEqual(design["requirements"]["REQ-2.1"], [])

    def test_header_page_without_a_designed_header_is_rejected(self):
        # Added beyond the plan: otherwise the final contract check fails on every header page after all worker calls.
        bad = answer("p2"); bad["pages"]["home"]["layout"] = True
        _, model = self.plan([bad, answer("p2")])
        self.assertEqual(model.calls, 2)
        self.assertIn("pages.home: layout is true but layout.elements is empty", model.requests[1][1]["correction"]["error"])
