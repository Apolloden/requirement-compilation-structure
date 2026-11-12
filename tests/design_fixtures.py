"""Shared helpers for the plan-stage tests: the real BookStack lite design as fixture data.

Fixture data lives only under tests/fixtures/; no pipeline code reads it."""

import copy
import json
from pathlib import Path

from infrastructure.stages.requirements.tree import RequirementTree

LITE = Path(__file__).parent / "fixtures" / "bookstack_lite_design"
LITE2 = Path(__file__).parent / "fixtures" / "bookstack_lite2_design"


def load(name: str, root: Path = LITE) -> dict:
    """One JSON file of a fixture design, freshly parsed."""
    return json.loads((root / name).read_text(encoding="utf-8"))


def lite_tree() -> RequirementTree:
    return RequirementTree(load("requirements.json"))


def artifacts(root: Path = LITE) -> dict:
    """The six design files as keyword arguments for check_artifacts (independent copies)."""
    return copy.deepcopy({
        "requirements": load("requirements.json", root),
        "schema": load("db_schema.json", root),
        "obligations": load("db_obligations.json", root),
        "backend": load("backend_apis.json", root),
        "ui": load("ui_design.json", root),
        "triggers": load("frontend_triggers.json", root),
    })


REPLAY_ORDER = ["p1", "p2", "d_REQ-1", "d_REQ-2", "d_REQ-3", "u_REQ-1", "u_REQ-2", "u_REQ-3",
    "t_REQ-1", "t_REQ-2", "t_REQ-3"]


def answer(name: str) -> dict:
    """One sliced model answer, for example answer("d_REQ-2")."""
    return load(f"answers/{name}.json")


class ReplayModel:
    """A fake text model that returns the given answers in order and records every request."""
    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.requests = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    def generate_json(self, instructions, request):
        # Copy the request: the proposal loop changes one request dict between attempts.
        self.requests.append((instructions, copy.deepcopy(request)))
        if not self.answers:
            raise AssertionError("no answer left")
        return copy.deepcopy(self.answers.pop(0))


def lite_contexts(tree) -> dict:
    """Per leaf: the requirement, its own steps and (empty) image analyses."""
    return {leaf: {**tree.requirements[leaf], "steps": [tree.steps[s] for s in tree.own_steps[leaf]], "images": {}}
        for leaf in tree.leaves}


def lite_images() -> dict:
    """Every image declared in the fixture requirements: {image ID: {"id", "page", "reference"}}."""
    found = {}
    def visit(node):
        description = node.get("description")
        if isinstance(description, dict):
            for image in description.get("image") or []:
                found.setdefault(image["id"], {"id": image["id"], "page": image.get("page"), "reference": image.get("reference")})
        for child in node.get("children", []):
            visit(child)
    visit(load("requirements.json"))
    return found


def make_lite_workspace(directory: Path) -> tuple[Path, Path]:
    """(input_dir, arc_dir) as stage 01 leaves them for the lite fixture, with empty image files."""
    input_dir, arc_dir = Path(directory) / "input", Path(directory) / "output" / ".arc"
    arc_dir.mkdir(parents=True)
    (arc_dir / "requirements.json").write_text((LITE / "requirements.json").read_text(encoding="utf-8"), encoding="utf-8")
    (arc_dir / "visual_analysis.json").write_text("{}", encoding="utf-8")
    for image in lite_images().values():
        path = input_dir / image["reference"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return input_dir, arc_dir
