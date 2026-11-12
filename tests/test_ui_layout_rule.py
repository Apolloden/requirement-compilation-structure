import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from infrastructure.stages.design.planner import ui_skeleton
from infrastructure.stages.frontend.ui import implementation
from design_fixtures import answer, lite_tree, load
from test_design_planner import HEADER

class LayoutRule(unittest.TestCase):
    def test_classic_design_keeps_today_s_instructions(self):
        self.assertEqual(implementation.page_instructions(load("expected/ui_design.json")), implementation.INSTRUCTIONS)

    def test_design_with_a_header_gets_the_rule_and_the_element_list(self):
        plan = answer("p2"); plan["layout"]["elements"] = HEADER; plan["pages"]["home"]["layout"] = True
        design = ui_skeleton(plan, lite_tree())
        self.assertIn("ONE layout component in frontend/src/Layout.tsx", implementation.page_instructions(design))
        self.assertEqual(implementation.layout_elements(design, "home"), ["UI-home-layout-header", "UI-home-layout-nav-home"])
        self.assertEqual(implementation.layout_elements(design, "books"), [])

    def test_layout_file_is_a_reference_once_it_exists(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            self.assertNotIn(implementation.LAYOUT_PATH, implementation.page_references(output, "frontend/tests/ui/home.spec.ts"))
            (output / "frontend/src").mkdir(parents=True); (output / implementation.LAYOUT_PATH).write_text("export {}")
            references = implementation.page_references(output, "frontend/tests/ui/home.spec.ts")
            self.assertIn(implementation.LAYOUT_PATH, references)
            self.assertIn("frontend/src/ui-runtime.ts", references)
            self.assertIn("frontend/tests/ui/home.spec.ts", references)
