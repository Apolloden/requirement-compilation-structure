import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from infrastructure.stages.design.assemble import assemble, problem_leaves, write_design
from infrastructure.stages.design.contract import check_design
from design_fixtures import lite_tree, load
from test_design_workers import full_state

class Assemble(unittest.TestCase):
    def test_replayed_state_is_clean_and_matches_the_fixture(self):
        files, problems = assemble(full_state(), lite_tree(), load("requirements.json"))
        self.assertEqual(problems, [])
        self.assertEqual(sorted(files), ["backend_apis.json", "db_obligations.json", "db_schema.json",
            "frontend_triggers.json", "shared_conventions.json", "ui_design.json", "ui_plan.json"])
        self.assertEqual(files["backend_apis.json"]["apis"], load("backend_apis.json")["apis"])
        self.assertEqual(files["ui_design.json"]["elements"], load("expected/ui_design.json")["elements"])

    def test_written_files_pass_check_design(self):
        files, _ = assemble(full_state(), lite_tree(), load("requirements.json"))
        with TemporaryDirectory() as directory:
            arc = Path(directory)
            (arc / "requirements.json").write_text((Path(__file__).parent / "fixtures/bookstack_lite_design/requirements.json").read_text())
            write_design(arc, files)
            self.assertEqual(check_design(arc), [])

    def test_skipped_leaf_gets_empty_records_everywhere(self):
        state = full_state(); state["skipped"] = ["REQ-3.2"]
        files, problems = assemble(state, lite_tree(), load("requirements.json"))
        self.assertEqual(problems, [])
        self.assertEqual(files["backend_apis.json"]["requirements"]["REQ-3.2"], [])
        self.assertEqual(files["ui_design.json"]["requirements"]["REQ-3.2"], [])
        self.assertEqual(files["frontend_triggers.json"]["requirements"]["REQ-3.2"], [])
        self.assertEqual(files["db_obligations.json"]["requirements"]["REQ-3.2"], {"read": [], "write": []})

    def test_missing_keys_and_dangling_trigger_ids_are_repaired(self):
        state = full_state()
        del state["triggers"]["requirements"]["REQ-2.3"]
        state["triggers"]["requirements"]["REQ-2.4"].append("TR-GHOST")
        files, problems = assemble(state, lite_tree(), load("requirements.json"))
        self.assertEqual(problems, [])
        self.assertEqual(files["frontend_triggers.json"]["requirements"]["REQ-2.3"], [])
        self.assertNotIn("TR-GHOST", files["frontend_triggers.json"]["requirements"]["REQ-2.4"])

    def test_problem_tied_to_one_leaf_empties_that_leaf(self):
        state = full_state(); state["backend"]["apis"]["API-create-page"]["write"] = []
        files, problems = assemble(state, lite_tree(), load("requirements.json"))
        self.assertEqual(problems, [])
        self.assertEqual(files["ui_design.json"]["requirements"]["REQ-3.1"], [])
        self.assertNotEqual(files["ui_design.json"]["requirements"]["REQ-2.3"], [])

    def test_problem_without_a_leaf_is_returned(self):
        state = full_state(); state["ui"]["pages"]["home"]["root"] = "UI-ghost"
        _, problems = assemble(state, lite_tree(), load("requirements.json"))
        self.assertTrue(any("root 'UI-ghost' is not an element" in p for p in problems))

    def test_problem_leaves(self):
        tree = lite_tree()
        self.assertEqual(problem_leaves("backend_apis: obligation coverage: failed for REQ-3.1: ...", tree), {"REQ-3.1"})
        self.assertEqual(problem_leaves("frontend_triggers.steps[STEP-REQ-2.3-01-01]: unknown trigger X", tree), {"REQ-2.3"})
        self.assertEqual(problem_leaves("ui_design.pages.home: root 'UI-ghost' is not an element", tree), set())

    def test_written_files_with_a_skipped_leaf_pass_check_design(self):
        # Added: check_design must know which leaves the plan emptied on purpose; ui_plan.json records them.
        state = full_state(); state["skipped"] = ["REQ-3.2"]
        files, problems = assemble(state, lite_tree(), load("requirements.json"))
        self.assertEqual(problems, [])
        self.assertEqual(files["ui_plan.json"]["skipped"], ["REQ-3.2"])
        with TemporaryDirectory() as directory:
            arc = Path(directory)
            (arc / "requirements.json").write_text((Path(__file__).parent / "fixtures/bookstack_lite_design/requirements.json").read_text())
            write_design(arc, files)
            self.assertEqual(check_design(arc), [])
