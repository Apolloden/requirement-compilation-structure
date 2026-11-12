import shutil
import tempfile
import unittest
from pathlib import Path

from arcflash.spec import SpecError, load_spec
from tests.support import AGENT_ROOT, FIXTURES

LITE = FIXTURES / "specs" / "bookstack-lite"
NOTES = FIXTURES / "specs" / "notes"
SAMPLE = AGENT_ROOT / "input"

ODD_YAML = """\
id: ROOT
name: Odd
description: Root as a plain string.
children:
- name: Plain leaf
  description: A leaf whose description is a plain string.
- id: REQ-B
  name: Leaf with odd scenario
  description:
    text: Shows "Go"(role=button) and "{item}"(role=link).
    image:
    - id: IMG-X
      reference: reference/x.png
  scenarios:
  - name: No id here
    steps:
    - Given: g
      When: w
      Then: t
"""


class SpecTests(unittest.TestCase):
    def test_lite_tree(self):
        s = load_spec(LITE)
        self.assertEqual(s.leaves, ["REQ-1.1", "REQ-2.1", "REQ-2.2", "REQ-2.3", "REQ-2.4", "REQ-2.5", "REQ-3.1", "REQ-3.2"])
        self.assertEqual((len(s.scenarios), len(s.images), s.title), (12, 7, "BookStack knowledge base"))

    def test_sample_tree_and_shared_image(self):
        s = load_spec(SAMPLE)
        self.assertEqual((s.leaves, len(s.scenarios)), (["REQ-1", "REQ-2", "REQ-3"], 9))
        home = next(i for i in s.images if i.path == "reference/homepage.png")
        self.assertEqual(home.ids, ["IMG-HOMEPAGE", "IMG-LOGOUT"])

    def test_role_names(self):
        self.assertEqual(set(load_spec(LITE).role_names), {("link", "Books"), ("region", "Book list"),
            ("button", "Delete"), ("button", "Confirm Delete"), ("button", "Cancel")})

    def test_digest_keeps_every_text_and_is_stable(self):
        a, b = load_spec(LITE).digest, load_spec(LITE).digest
        self.assertEqual(a, b)
        for needle in ("2026-10-06T12:00:00Z", "Write your page content here...", "SCN-REQ-3.2-02",
                       "Uses: REQ-2.3", "GROUP REQ-2: Manage books", "Save Book"):
            self.assertIn(needle, a)
        self.assertNotIn("\\ref[", a)

    def test_digest_format(self):
        d = load_spec(NOTES).digest
        self.assertTrue(d.startswith("APPLICATION: Notes\nA small public notebook."))
        self.assertIn("REQ-1.1: Browse notes\nDepends on: REQ-1.2\nScreenshots: IMG-NOTES (page notes)\n", d)
        self.assertIn("  Step STEP-REQ-1.1-01-01\n    Given: A UI-created note Note 1.1 with body Body 1.1.\n", d)
        self.assertIn("\n\nREQUIREMENTS\nGroups give context; leaf requirements are what you build.\n\nGROUP REQ-1: Notes\nManage notes.\n\n", d)
        self.assertIn("Scenario SCN-REQ-1.3-01: Confirm deletion\n", d)

    def test_notes_and_assets(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "task"
            shutil.copytree(NOTES, folder)
            (folder / "assets").mkdir()
            (folder / "assets" / "logo.png").write_bytes(b"png")
            (folder / "prerequisites.md").write_text("Seeded user: alice\n")
            (folder / "requirements.md").write_text("ignored\n")
            (folder / "empty.txt").write_text("  \n")
            s = load_spec(folder)
        self.assertEqual(s.assets, ["logo.png"])
        self.assertIn("ASSETS (served at /assets/<name>): logo.png", s.digest)
        self.assertIn("Seeded user: alice", s.digest); self.assertNotIn("ignored", s.digest)
        self.assertIn("NOTES FROM THE TASK FOLDER\nFrom prerequisites.md:\nSeeded user: alice", s.digest)
        self.assertNotIn("empty.txt", s.digest)

    def test_odd_shapes_load(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "requirements.yaml").write_text(ODD_YAML)
            s = load_spec(Path(d))
        self.assertEqual(len(s.leaves), 2); self.assertTrue(s.scenarios[0].id.startswith("SCN-"))
        self.assertEqual(s.requirements[s.leaves[0]].text, "A leaf whose description is a plain string.")
        self.assertEqual((s.images[0].path, s.images[0].page), ("reference/x.png", ""))
        self.assertEqual(s.role_names, [("button", "Go")])
        self.assertTrue(s.scenarios[0].steps[0].id.startswith("STEP-"))

    def test_missing_yaml(self):
        with self.assertRaisesRegex(SpecError, "requirements.yaml"): load_spec(Path(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
