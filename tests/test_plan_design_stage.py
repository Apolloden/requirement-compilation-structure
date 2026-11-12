import os, unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from infrastructure import compiler
from infrastructure.pipeline import plan_design as stage
from infrastructure.stages.design.contract import check_design
from infrastructure.utils.presentation.stages import stage_label
from design_fixtures import REPLAY_ORDER, ReplayModel, answer, make_lite_workspace

CLASSIC = ["build_db_schema", "infer_db_obligations", "design_backend_apis", "design_frontend_ui", "design_frontend_triggers"]

class PlanStage(unittest.TestCase):
    def run_stage(self, model, classic_effect=None):
        with TemporaryDirectory() as directory:
            input_dir, arc = make_lite_workspace(Path(directory))
            order = []
            mocks = {name: Mock(side_effect=lambda *a, _n=name, **k: (order.append(_n), classic_effect and classic_effect(_n))[0]) for name in CLASSIC}
            with patch.object(stage, "TextModel", return_value=model), patch.multiple(stage, **mocks):
                report = stage.plan_design(input_dir, arc)
            return report, order, sorted(p.name for p in arc.iterdir()), check_design(arc) if (arc / "db_schema.json").is_file() else None

    def test_replay_writes_a_clean_design_in_eleven_calls(self):
        model = ReplayModel([answer(name) for name in REPLAY_ORDER])
        report, order, files, problems = self.run_stage(model)
        self.assertEqual((report["status"], report["path"], report["calls"], report["skipped"]), ("GREEN", "arcfable", 11, []))
        self.assertEqual(order, [])
        self.assertEqual(problems, [])
        for name in ("db_schema.json", "db_obligations.json", "backend_apis.json", "ui_design.json",
                     "frontend_triggers.json", "shared_conventions.json", "ui_plan.json"):
            self.assertIn(name, files)

    def test_failed_data_plan_runs_the_five_classic_stages_in_order(self):
        report, order, files, _ = self.run_stage(ReplayModel([{}, {}, {}]))
        self.assertEqual(order, CLASSIC)
        self.assertEqual(report["path"], "classic")
        self.assertTrue(report["plan_problems"])
        self.assertNotIn("ui_plan.json", files)
        self.assertNotIn("db_schema.json", files)        # nothing from the plan was written

    def test_unexpected_exception_also_ends_in_the_classic_stages(self):       # Review Focus 4
        model = ReplayModel([answer(name) for name in REPLAY_ORDER])
        with patch.object(stage, "assemble", side_effect=KeyError("boom")):
            report, order, _, _ = self.run_stage(model)
        self.assertEqual((report["path"], order), ("classic", CLASSIC))

    def test_one_failing_classic_stage_does_not_stop_the_others(self):
        def effect(name):
            if name == "infer_db_obligations": raise RuntimeError("503")
        report, order, _, _ = self.run_stage(ReplayModel([{}, {}, {}]), effect)
        self.assertEqual(order, CLASSIC)
        self.assertEqual(report["status"], "COMPLETED_WITH_FAILURES")
        self.assertEqual(report["failures"], [{"stage": "infer_db_obligations", "error": "503"}])

    def test_skipped_leaf_is_reported(self):
        answers = [answer(name) for name in REPLAY_ORDER]
        t3 = REPLAY_ORDER.index("t_REQ-3")
        # the group call fails 3 times, then REQ-3.1 alone fails 3 times, then REQ-3.2 alone fails 3 times
        report, order, _, problems = self.run_stage(ReplayModel(answers[:t3] + [[]] * 9))
        self.assertEqual((report["status"], report["path"]), ("COMPLETED_WITH_FAILURES", "arcfable"))
        self.assertEqual(sorted(report["skipped"]), ["REQ-3.1", "REQ-3.2"])
        self.assertEqual((order, problems), ([], []))

class Switch(unittest.TestCase):
    def names(self, value):
        env = {} if value is None else {"ARC_DESIGN": value}
        with patch.dict(os.environ, env, clear=False):
            if value is None: os.environ.pop("ARC_DESIGN", None)
            return [name for name, _, _ in compiler.active_pipeline()]

    def test_default_is_arcfable(self):
        expected = ["00_project_initialization", "01_explore_requirement", "02_plan_design", "04_implement_db",
            "06_implement_backend_tdd", "08_implement_ui", "10_implement_frontend_tdd"]
        for value in (None, "", "arcfable", "anything"):
            self.assertEqual(self.names(value), expected)

    def test_classic_ignores_case_and_spaces(self):                              # Review Focus 5
        for value in ("classic", " Classic ", "CLASSIC"):
            self.assertEqual(len(self.names(value)), 11)
            self.assertNotIn("02_plan_design", self.names(value))

    def test_every_stage_can_be_selected_by_name(self):
        names = [name for name, _, _ in compiler.PIPELINE]
        self.assertEqual(len(names), 12)
        self.assertIn("02_plan_design", names); self.assertIn("07_design_ui", names)

    def test_labels(self):
        self.assertEqual(stage_label("02_plan_design"), "Stage 02 | Plan the whole design")
        self.assertEqual(stage_label("02_build_db_schema"), "Stage 02 | Design database schema")
