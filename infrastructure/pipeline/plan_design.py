"""Stage 02 (ArcFable): plan the whole design, then hand the build stages checked design files.

Replaces the classic design stages 02, 03, 05, 07 and 09. P1 and P2 see the whole app; the
data, UI and trigger workers fill in each requirement group; the assembler checks the result
against the contract before anything is written. If the plan cannot be completed, the
classic stages run instead (spec section 6, level 2)."""

from functools import partial
from pathlib import Path

from infrastructure.pipeline.backend_apis import design_backend_apis
from infrastructure.pipeline.db_obligations import infer_db_obligations
from infrastructure.pipeline.db_schema import build_db_schema
from infrastructure.pipeline.frontend_triggers import design_frontend_triggers
from infrastructure.pipeline.ui_design import design_frontend_ui
from infrastructure.shared.models.text import PLANNER_MODEL, TextModel
from infrastructure.stages.design.assemble import assemble, write_design
from infrastructure.stages.design.groups import requirement_groups
from infrastructure.stages.design.planner import PLAN_OUTPUT_CAP, plan_data, plan_ui
from infrastructure.stages.design.workers import (data_worker, new_state, run_workers, trigger_worker, ui_worker,
    with_ui_plan)
from infrastructure.stages.frontend.context import load_ui_context
from infrastructure.utils.files import read_artifact
from infrastructure.utils.presentation.console import DemoConsole


class CountingModel:
    """Forward every request to the model and count the calls."""
    def __init__(self, model) -> None:
        self.model = model
        self.calls = 0

    def generate_json(self, instructions, request):
        self.calls += 1
        return self.model.generate_json(instructions, request)


def run_plan(input_dir: Path, arc_dir: Path, model, console) -> tuple[dict, list[str], dict]:
    """(files, problems, counts): P1, P2, the three worker kinds per group, then assemble."""
    counter = CountingModel(model)
    tree, images, contexts = load_ui_context(arc_dir, input_dir)
    groups = requirement_groups(tree)
    data_plan = plan_data(tree, counter, console)
    state = new_state(data_plan, tree)
    state = with_ui_plan(state, plan_ui(tree, contexts, images, data_plan, counter, console), tree)
    state = run_workers(partial(data_worker, tree=tree, model=counter, console=console), state, groups, console)
    state = run_workers(partial(ui_worker, tree=tree, contexts=contexts, model=counter, console=console),
        state, groups, console)
    state = run_workers(partial(trigger_worker, tree=tree, contexts=contexts, model=counter, console=console),
        state, groups, console)
    files, problems = assemble(state, tree, read_artifact(arc_dir, "requirements.json"))
    return files, problems, {"calls": counter.calls, "skipped": files["ui_plan.json"]["skipped"]}


def classic_design(input_dir: Path, arc_dir: Path, pause: bool = False) -> list[dict]:
    """Run the five classic design stages in order; one failing stage does not stop the next."""
    (Path(arc_dir) / "ui_plan.json").unlink(missing_ok=True)
    failures = []
    for name, run in (("build_db_schema", lambda: build_db_schema(arc_dir, pause)),
                      ("infer_db_obligations", lambda: infer_db_obligations(arc_dir, pause)),
                      ("design_backend_apis", lambda: design_backend_apis(arc_dir, pause)),
                      ("design_frontend_ui", lambda: design_frontend_ui(input_dir, arc_dir, pause)),
                      ("design_frontend_triggers", lambda: design_frontend_triggers(input_dir, arc_dir, pause))):
        try:
            run()
        except Exception as error:
            failures.append({"stage": name, "error": str(error)})
    return failures


def plan_design(input_dir: Path, arc_dir: Path, pause: bool = False) -> dict:
    """The stage: write a checked plan, or fall back to the classic design stages."""
    try:
        console = DemoConsole(pause)
        model = TextModel(name=PLANNER_MODEL, max_output_tokens=PLAN_OUTPUT_CAP, max_size_errors=2)
        files, problems, counts = run_plan(Path(input_dir), Path(arc_dir), model, console)
    except Exception as error:
        problems = [f"{type(error).__name__}: {error}"]
    if not problems:
        write_design(Path(arc_dir), files)
        report = {"status": "GREEN", "path": "arcfable", "calls": counts["calls"], "skipped": counts["skipped"]}
        if counts["skipped"]:
            report["status"] = "COMPLETED_WITH_FAILURES"
            report["failures"] = [{"task": leaf, "status": "FAILED", "error": "skipped by the plan stage"}
                for leaf in counts["skipped"]]
        return report
    print("Plan stage could not complete; running the classic design stages.\n" + "\n".join(problems), flush=True)
    failures = classic_design(Path(input_dir), Path(arc_dir), pause)
    return {"status": "COMPLETED_WITH_FAILURES" if failures else "GREEN", "path": "classic",
        "plan_problems": problems, "failures": failures}
