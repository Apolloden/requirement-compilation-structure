import unittest
from design_fixtures import REPLAY_ORDER, answer, load, lite_tree

class FixtureAnswers(unittest.TestCase):
    def test_every_answer_exists_and_covers_the_design(self):
        tree, backend, ui, triggers = lite_tree(), load("backend_apis.json"), load("expected/ui_design.json"), load("expected/frontend_triggers.json")
        answers = {name: answer(name) for name in REPLAY_ORDER}
        self.assertEqual(set(answers["p1"]["apis"]), set(backend["apis"]))
        self.assertEqual(set(answers["p2"]["pages"]), set(ui["pages"]))
        emitted_apis = [aid for g in ("REQ-1", "REQ-2", "REQ-3") for aid in answers[f"d_{g}"]["apis"]]
        self.assertEqual(sorted(emitted_apis), sorted(backend["apis"]))           # each API exactly once
        roots = {page["root"] for page in ui["pages"].values()}
        emitted = [uid for g in ("REQ-1", "REQ-2", "REQ-3") for uid in answers[f"u_{g}"]["elements"]]
        self.assertEqual(sorted(emitted), sorted(set(ui["elements"]) - roots))    # each element exactly once
        emitted_triggers = [tid for g in ("REQ-1", "REQ-2", "REQ-3") for tid in answers[f"t_{g}"]["triggers"]]
        self.assertEqual(sorted(emitted_triggers), sorted(triggers["triggers"]))
        self.assertEqual({rid for g in ("REQ-1", "REQ-2", "REQ-3") for rid in answers[f"d_{g}"]["requirement_apis"]}, set(tree.leaves))

    def test_roots_are_renamed(self):
        ui = load("expected/ui_design.json")
        self.assertEqual({pid: page["root"] for pid, page in ui["pages"].items()}, {pid: f"UI-{pid}" for pid in ui["pages"]})
        self.assertNotIn("UI-books-page", ui["elements"])
