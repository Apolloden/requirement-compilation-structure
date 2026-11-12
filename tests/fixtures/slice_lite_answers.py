"""One-off script: cut the first-try lite design into the answers each plan-stage call would give.

Run once from the agent folder; its output is committed:
    PYTHONPATH=.:tests .venv/bin/python tests/fixtures/slice_lite_answers.py

Writes, into tests/fixtures/bookstack_lite_design/:
  expected/ui_design.json, expected/frontend_triggers.json  the design with page roots renamed UI-<page ID>
  answers/p1.json, answers/p2.json                          the two whole-app answers
  answers/{d,u,t}_<group>.json                              one data, UI and trigger answer per group
"""

import copy
import json
from pathlib import Path

from design_fixtures import LITE, load, lite_tree
from infrastructure.stages.design.groups import requirement_groups
from infrastructure.stages.design.names import named_elements


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rename_roots(ui: dict, triggers: dict) -> tuple[dict, dict]:
    """Every page root becomes UI-<page ID>, the ID the header insertion uses."""
    ui, triggers = copy.deepcopy(ui), copy.deepcopy(triggers)
    renamed = {page["root"]: f"UI-{pid}" for pid, page in ui["pages"].items()}
    clashes = [new for old, new in renamed.items() if new in ui["elements"] and new != old]
    assert not clashes, f"renamed roots clash with existing elements: {clashes}"
    swap = lambda uid: renamed.get(uid, uid)
    for page in ui["pages"].values():
        page["root"] = swap(page["root"])
    ui["elements"] = {swap(uid): {**node, "parent": swap(node.get("parent"))} for uid, node in ui["elements"].items()}
    for kind in ("requirements", "steps"):
        ui[kind] = {key: [swap(uid) for uid in ids] for key, ids in ui[kind].items()}
    for trigger in triggers["triggers"].values():
        trigger["source"] = swap(trigger["source"])
        for action in trigger["action"]:
            if action.get("kind") == "ui":
                action["ui"] = swap(action["ui"])
    return ui, triggers


def subtree(ui: dict, pid: str) -> list[str]:
    """Element IDs under one page root (root included), in design order."""
    root, found = ui["pages"][pid]["root"], []
    for uid in ui["elements"]:
        current = uid
        while current is not None:
            if current == root:
                found.append(uid)
                break
            current = ui["elements"][current].get("parent")
    return found


def main() -> None:
    tree = lite_tree()
    schema, obligations, backend = load("db_schema.json"), load("db_obligations.json"), load("backend_apis.json")
    ui, triggers = rename_roots(load("ui_design.json"), load("frontend_triggers.json"))
    save(LITE / "expected" / "ui_design.json", ui)
    save(LITE / "expected" / "frontend_triggers.json", triggers)
    groups = requirement_groups(tree)
    answers = LITE / "answers"

    save(answers / "p1.json", {"entities": schema["entities"],
        "apis": {aid: {"description": api["description"], "signature": api["signature"]} for aid, api in backend["apis"].items()}})

    def leaf_elements(leaf):
        ids = set(ui["requirements"][leaf])
        for sid in tree.own_steps[leaf]:
            ids |= set(ui["steps"][sid])
        return ids

    pages = {}
    for pid, page in ui["pages"].items():
        on_page = set(subtree(ui, pid))
        pages[pid] = {"name": page["name"], "route": page["route"], "images": page["images"], "layout": False,
            "requirements": [leaf for leaf in tree.leaves if leaf_elements(leaf) & on_page]}
    naming = {}
    for pid in ui["pages"]:
        for _, role, name in named_elements(ui, pid):
            if role in ("link", "button"):
                naming.setdefault((name, role), []).append(pid)
    save(answers / "p2.json", {"pages": pages, "layout": {"elements": {}},
        "naming": [{"name": name, "role": role, "pages": pids} for (name, role), pids in naming.items()]})

    apis_done, elements_done, triggers_done = set(), set(), set()
    roots = {page["root"] for page in ui["pages"].values()}
    for group in groups:
        leaves = group["leaves"]
        steps = [sid for leaf in leaves for sid in tree.own_steps[leaf]]
        used = [aid for aid in backend["apis"] if any(aid in backend["requirements"][leaf] for leaf in leaves)]
        new_apis = [aid for aid in used if aid not in apis_done]
        apis_done |= set(new_apis)
        save(answers / f"d_{group['id']}.json", {
            "requirement_fields": {leaf: schema["requirements"][leaf] for leaf in leaves},
            "obligations": {"requirements": {leaf: obligations["requirements"][leaf] for leaf in leaves},
                            "steps": {sid: obligations["steps"][sid] for sid in steps}},
            "apis": {aid: backend["apis"][aid] for aid in new_apis},
            "requirement_apis": {leaf: backend["requirements"][leaf] for leaf in leaves}})

        group_pages = [pid for pid, page in pages.items() if set(page["requirements"]) & set(leaves)]
        new_elements = [uid for pid in group_pages for uid in subtree(ui, pid) if uid not in roots and uid not in elements_done]
        new_elements = list(dict.fromkeys(new_elements))
        elements_done |= set(new_elements)
        save(answers / f"u_{group['id']}.json", {
            "elements": {uid: ui["elements"][uid] for uid in ui["elements"] if uid in set(new_elements)},
            "pages": {},
            "requirement_ui": {leaf: ui["requirements"][leaf] for leaf in leaves},
            "step_ui": {sid: ui["steps"][sid] for sid in steps}})

        listed = [tid for key in [*leaves, *steps]
            for tid in (triggers["requirements"].get(key, []) if key in leaves else triggers["steps"].get(key, []))]
        new_triggers = [tid for tid in dict.fromkeys(listed) if tid not in triggers_done]
        triggers_done |= set(new_triggers)
        save(answers / f"t_{group['id']}.json", {
            "triggers": {tid: triggers["triggers"][tid] for tid in new_triggers},
            "requirement_triggers": {leaf: triggers["requirements"][leaf] for leaf in leaves},
            "step_triggers": {sid: triggers["steps"][sid] for sid in steps}})


if __name__ == "__main__":
    main()
