import unittest
from unittest.mock import Mock
from infrastructure.stages.design import planner
from design_fixtures import ReplayModel, answer, lite_tree

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
