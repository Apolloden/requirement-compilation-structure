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

def ui_state(upto=("REQ-1", "REQ-2", "REQ-3")):
    from design_fixtures import lite_contexts
    tree = lite_tree(); state = workers.with_ui_plan(data_state(), answer("p2"), tree)
    for gid in upto:
        state = workers.ui_worker(state, GROUPS[gid], tree, lite_contexts(tree), ReplayModel([answer(f"u_{gid}")]), Mock())
    return state

class UiWorker(unittest.TestCase):
    def run_bad(self, gid, change, upto):
        from design_fixtures import lite_contexts
        tree = lite_tree(); state = ui_state(upto)
        bad = answer(f"u_{gid}"); change(bad)
        model = ReplayModel([bad, answer(f"u_{gid}")])
        workers.ui_worker(state, GROUPS[gid], tree, lite_contexts(tree), model, Mock())
        return model.requests[1][1]["correction"]["error"]

    def test_real_answers_reproduce_the_design(self):
        from design_fixtures import load
        state = ui_state(); expected = load("expected/ui_design.json")
        self.assertEqual(state["ui"]["elements"], expected["elements"])
        self.assertEqual(state["ui"]["requirements"], expected["requirements"])
        self.assertEqual(state["ui"]["steps"], expected["steps"])

    def test_new_page_is_rejected(self):
        error = self.run_bad("REQ-2", lambda a: a["pages"].update(extra={"name": "X", "route": "/x", "root": "UI-x", "images": []}), ("REQ-1",))
        self.assertIn("Pages are fixed by the UI plan", error)

    def test_second_link_with_a_listed_name_is_rejected(self):
        def change(a):
            a["elements"]["UI-book-extra-books"] = {"type": "link", "parent": "UI-book", "mode": [], "data": [],
                "prop": {"label": "Books", "href": "/books"}}
        self.assertIn("2 links named 'Books'", self.run_bad("REQ-2", change, ("REQ-1",)))

    def test_link_that_is_not_in_the_naming_table_is_rejected(self):
        def change(a):
            a["elements"]["UI-book-settings"] = {"type": "link", "parent": "UI-book", "mode": [], "data": [],
                "prop": {"label": "Settings", "href": "/settings"}}
        self.assertIn("is not in the naming table", self.run_bad("REQ-2", change, ("REQ-1",)))

    def test_missing_listed_control_is_reported_when_the_page_is_complete(self):
        def change(a):
            victim = next(uid for uid, n in a["elements"].items() if n["type"] == "button" and n["prop"].get("label") == "Save Page"
                and not any(other.get("parent") == uid for other in a["elements"].values()))
            del a["elements"][victim]
            for ids in [*a["requirement_ui"].values(), *a["step_ui"].values()]:
                if victim in ids: ids.remove(victim)
        self.assertIn("no button named 'Save Page' (naming table)", self.run_bad("REQ-3", change, ("REQ-1", "REQ-2")))

    def test_requirement_without_ui_is_rejected(self):
        error = self.run_bad("REQ-3", lambda a: a["requirement_ui"].update({"REQ-3.2": []}), ("REQ-1", "REQ-2"))
        self.assertIn("REQ-3.2", error)
