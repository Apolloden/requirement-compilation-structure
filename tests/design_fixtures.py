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
