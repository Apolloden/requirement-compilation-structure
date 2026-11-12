"""Assemble the plan state into the five design files, repair only what is safe, check, write.

The safe repairs never invent content: they give deliberately skipped requirements empty
records, fill missing keys with empty lists and drop references to triggers that do not
exist. Anything else the contract check finds is either tied to a requirement (which is
then emptied, once) or returned as a problem, and the files are not written."""

from copy import deepcopy
import re
from pathlib import Path

from infrastructure.stages.design.contract import check_artifacts
from infrastructure.stages.database.obligations.records import normalize_obligations
from infrastructure.stages.frontend.triggers.design import empty_triggers
from infrastructure.stages.frontend.ui.design import normalize_design
from infrastructure.utils.files import save_json

EMPTY = {"read": [], "write": []}


def mentioned(text: str, identity: str) -> bool:
    """True when the text names the ID as a whole (REQ-3.1 does not match REQ-3.10 or STEP-REQ-3.1-01)."""
    return re.search(r"(?<![\w.-])" + re.escape(identity) + r"(?![\w-]|\.\d)", text) is not None


def problem_leaves(problem: str, tree) -> set[str]:
    """Leaf IDs a problem names, plus the owning leaf of every step ID it names."""
    found = {leaf for leaf in tree.leaves if mentioned(problem, leaf)}
    for leaf in tree.leaves:
        if any(mentioned(problem, sid) for sid in tree.own_steps[leaf]):
            found.add(leaf)
    return found


def repaired(state: dict, tree) -> dict:
    """Safe repairs 1-3 on a copy of the state."""
    state = deepcopy(state)
    skipped = state["skipped"]
    schema, obligations, backend = state["schema"], state["obligations"], state["backend"]
    ui = state["ui"] or {"pages": {}, "elements": {}, "requirements": {}, "steps": {}}
    triggers = {**empty_triggers(tree), **(state["triggers"] or {})}
    triggers = {"triggers": triggers["triggers"],
        "requirements": {**empty_triggers(tree)["requirements"], **triggers["requirements"]},
        "steps": {**empty_triggers(tree)["steps"], **triggers["steps"]}}
    for leaf in skipped:                                                     # 1. skipped leaves: empty records
        schema["requirements"][leaf] = []
        obligations["requirements"][leaf] = dict(EMPTY)
        backend["requirements"][leaf] = []
        ui["requirements"][leaf] = []
        triggers["requirements"][leaf] = []
        for sid in tree.own_steps[leaf]:
            obligations["steps"][sid] = dict(EMPTY)
            ui["steps"][sid] = []
            triggers["steps"][sid] = []
    ui = normalize_design(ui, tree)                                          # 2. missing keys: []
    for kind in ("requirements", "steps"):                                   # 3. dangling trigger IDs
        triggers[kind] = {key: [tid for tid in ids if tid in triggers["triggers"]] for key, ids in triggers[kind].items()}
    state["ui"], state["triggers"] = ui, triggers
    return state


def files_of(state: dict, tree) -> dict:
    """The seven files the build stages and later checks read."""
    obligations = state["obligations"]
    filled = {"requirements": {rid: obligations["requirements"].get(rid, EMPTY) for rid in tree.requirements},
        "steps": {sid: obligations["steps"].get(sid, EMPTY) for sid in tree.steps}}
    return deepcopy({
        "db_schema.json": state["schema"],
        "db_obligations.json": normalize_obligations(filled, tree),               # 4. saved normalized
        "backend_apis.json": state["backend"],
        "ui_design.json": state["ui"],
        "frontend_triggers.json": state["triggers"],
        "shared_conventions.json": state["conventions"],
        "ui_plan.json": state["ui_plan"],
    })


def check(state: dict, requirements: dict) -> list[str]:
    return check_artifacts(requirements, state["schema"], state["obligations"], state["backend"], state["ui"],
        state["triggers"], plan=state["ui_plan"], skipped=frozenset(state["skipped"]))


def assemble(state: dict, tree, requirements: dict) -> tuple[dict, list[str]]:
    """(files, problems); files are safe to write only when problems is []."""
    current = repaired(state, tree)
    problems = check(current, requirements)
    if problems:
        tied = [problem_leaves(problem, tree) for problem in problems]
        leaves = set().union(*tied)
        if leaves and all(tied):
            # Every problem belongs to a requirement: empty those requirements and check once more.
            current = repaired({**current, "skipped": [*current["skipped"],
                *sorted(leaf for leaf in leaves if leaf not in current["skipped"])]}, tree)
            problems = check(current, requirements)
    return files_of(current, tree), problems


def write_design(arc_dir: Path, files: dict) -> None:
    for name, content in files.items():
        save_json(Path(arc_dir) / name, content)
