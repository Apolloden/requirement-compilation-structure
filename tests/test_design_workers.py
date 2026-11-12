import unittest
from unittest.mock import Mock
from infrastructure.stages.design import workers
from design_fixtures import ReplayModel, answer, lite_tree

GROUPS = {"REQ-1": ["REQ-1.1"], "REQ-2": ["REQ-2.1", "REQ-2.2", "REQ-2.3", "REQ-2.4", "REQ-2.5"], "REQ-3": ["REQ-3.1", "REQ-3.2"]}

def data_state(upto=("REQ-1", "REQ-2", "REQ-3")):
    tree = lite_tree()
    state = workers.new_state(answer("p1"), tree)
    for gid in upto:
        state = workers.data_worker(state, GROUPS[gid], tree, ReplayModel([answer(f"d_{gid}")]), Mock())
    return state

class DataWorker(unittest.TestCase):
    def test_real_answers_reproduce_schema_obligations_and_apis(self):
        from design_fixtures import load
        state = data_state()
        self.assertEqual(state["backend"]["apis"], load("backend_apis.json")["apis"])
        self.assertEqual(state["backend"]["requirements"]["REQ-2.3"], ["API-create-book", "API-get-book", "API-list-books"])
        self.assertEqual(state["schema"]["requirements"]["REQ-3.1"], load("db_schema.json")["requirements"]["REQ-3.1"])
        self.assertEqual(state["done"]["data"], lite_tree().leaves)

    def test_the_input_state_is_not_changed(self):
        tree = lite_tree(); before = workers.new_state(answer("p1"), tree)
        workers.data_worker(before, GROUPS["REQ-1"], tree, ReplayModel([answer("d_REQ-1")]), Mock())
        self.assertEqual(before["done"]["data"], [])

    def test_changed_signature_is_sent_back(self):
        tree = lite_tree(); state = data_state(("REQ-1",))
        bad = answer("d_REQ-2"); bad["apis"]["API-create-book"]["signature"]["path"] = "/api/book"
        model = ReplayModel([bad, answer("d_REQ-2")])
        workers.data_worker(state, GROUPS["REQ-2"], tree, model, Mock())
        self.assertEqual(model.calls, 2)
        self.assertIn("API-create-book", model.requests[1][1]["correction"]["error"])

    def test_missing_write_coverage_is_sent_back(self):
        tree = lite_tree(); state = data_state(("REQ-1",))
        bad = answer("d_REQ-2"); bad["apis"]["API-create-book"]["write"] = []
        model = ReplayModel([bad, answer("d_REQ-2")])
        workers.data_worker(state, GROUPS["REQ-2"], tree, model, Mock())
        self.assertEqual(model.calls, 2)

    def test_wrong_shape_answers_are_repaired_not_crashed(self):       # Review Focus 3
        tree = lite_tree(); state = data_state(("REQ-1",))
        missing_key = answer("d_REQ-2"); del missing_key["requirement_apis"]["REQ-2.4"]
        model = ReplayModel([[], missing_key, answer("d_REQ-2")])
        workers.data_worker(state, GROUPS["REQ-2"], tree, model, Mock())
        self.assertEqual(model.calls, 3)

    def test_three_bad_answers_raise(self):
        tree = lite_tree(); state = data_state(("REQ-1",))
        with self.assertRaisesRegex(RuntimeError, "exhausted 3 proposals"):
            workers.data_worker(state, GROUPS["REQ-2"], tree, ReplayModel([[], [], []]), Mock())
