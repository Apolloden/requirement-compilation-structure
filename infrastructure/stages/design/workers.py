"""Group workers of the plan stage: data (D), UI (U) and triggers (T).

Each worker designs the details for the requirements of one group in one model call. The
plan state is one dict passed from worker to worker; a worker returns a new dict and never
changes the one it receives, so a failed call leaves the earlier state intact."""

from copy import deepcopy
import re

from infrastructure.pipeline.backend_apis import INSTRUCTIONS as API_INSTRUCTIONS
from infrastructure.pipeline.db_obligations import INSTRUCTIONS as OBLIGATION_INSTRUCTIONS
from infrastructure.shared.agent.conventions import CONVENTION_RULES, merge_conventions, select_conventions
from infrastructure.shared.agent.proposals import propose_json
from infrastructure.stages.backend.primitives import validate_design
from infrastructure.stages.database.obligations.records import check_symbols, normalize_inference, normalize_obligations
from infrastructure.stages.database.schema import check_related_fields
from infrastructure.stages.design.contract import API_KEYS, check_naming, check_ui
from infrastructure.stages.design.planner import (PLAN_ATTEMPTS, check_api_list, reject, requirement_records,
    ui_skeleton)
from infrastructure.stages.frontend.triggers.design import (api_ids, apply_decomposition, empty_triggers,
    relevant_pages, validate_coverage)
from infrastructure.stages.frontend.triggers.prompts import INSTRUCTIONS as TRIGGER_INSTRUCTIONS
from infrastructure.stages.frontend.ui.design import INSTRUCTIONS as UI_INSTRUCTIONS, apply_proposal, page_context

GROUP_HEADER = """This call covers several requirements of one group. The rule texts below were written for
one requirement at a time: apply them to each supplied requirement. Where they describe a
return format, use the OUTPUT FORMAT at the end of these instructions instead."""

D_OUTPUT = """OUTPUT FORMAT
{"requirement_fields": {"REQ-ID": ["Entity.field"]},
 "obligations": {"requirements": {"REQ-ID": {"read": [], "write": []}},
                 "steps": {"STEP-ID": {"read": [], "write": []}}},
 "apis": {"API-ID": {complete API record}},
 "requirement_apis": {"REQ-ID": ["API-ID"]}}
Include every supplied requirement ID and every supplied step ID. The schema is fixed: use
its exact names and do not define entities or fields. api_plan lists the planned
operations: keep each one's method, path and function name. Return a complete record for
every planned API your requirements use that is not yet in apis. Return an existing API
only when your requirements need it changed, and keep earlier requirements covered. Add
an API that is not in api_plan only when no planned one fits."""

D_INSTRUCTIONS = (GROUP_HEADER + "\n\nRULES FOR READ AND WRITE RECORDS\n" + OBLIGATION_INSTRUCTIONS
    + "\n\nRULES FOR APIS\n" + API_INSTRUCTIONS + "\n\n" + CONVENTION_RULES + "\n\n" + D_OUTPUT)

EMPTY = {"read": [], "write": []}


def new_state(data_plan: dict, tree) -> dict:
    """The plan state after P1: tables and planned APIs, nothing filled in per requirement yet."""
    folders = [rid for rid in tree.requirements if tree.children[rid]]
    conventions = {}
    if data_plan.get("conventions"):
        conventions = merge_conventions({}, {"conventions": data_plan["conventions"]}, tree.leaves[0], [])
    return deepcopy({
        "schema": {"entities": data_plan["entities"], "requirements": {}},
        "api_plan": data_plan["apis"],
        "obligations": {"requirements": {rid: EMPTY for rid in folders},
                        "steps": {sid: EMPTY for rid in folders for sid in tree.own_steps[rid]}},
        "backend": {"apis": {}, "requirements": {rid: [] for rid in folders}},
        "conventions": conventions,
        "ui_plan": None, "ui": None, "triggers": empty_triggers(tree),
        "done": {"data": [], "ui": [], "triggers": []},
        "skipped": [],
    })


def require_sections(answer, sections: dict) -> None:
    """Every expected key of every section is present; one error lists all that are missing."""
    if not isinstance(answer, dict):
        raise ValueError("Return one JSON object in the OUTPUT FORMAT, not " + type(answer).__name__)
    problems = []
    for path, keys in sections.items():
        value = answer
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if not isinstance(value, dict):
            problems.append(f"{path} must be an object")
            continue
        missing = [key for key in keys if key not in value]
        if missing:
            problems.append(f"{path}: missing {', '.join(missing)}")
    reject(problems)


def data_worker(state: dict, leaves: list[str], tree, model, console) -> dict:
    """D: read/write records, related fields and complete API records for one group's requirements."""
    steps = [sid for leaf in leaves for sid in tree.own_steps[leaf]]
    schema = state["schema"]
    request = {"requirements": requirement_records(tree, leaves), "schema": {"entities": schema["entities"]},
        "api_plan": state["api_plan"], "apis": state["backend"]["apis"],
        "shared_conventions": {key: record["description"] for key, record in state["conventions"].items()}}

    def accept(answer):
        require_sections(answer, {"requirement_fields": leaves, "obligations.requirements": leaves,
            "obligations.steps": steps, "requirement_apis": leaves, "apis": []})
        problems, inferred = [], {}
        for leaf in leaves:
            try:
                check_related_fields(schema, answer["requirement_fields"][leaf])
            except ValueError as error:
                problems.append(f"requirement_fields[{leaf}]: {error}")
            try:
                inferred[leaf] = normalize_inference({"requirement": answer["obligations"]["requirements"][leaf],
                    "steps": answer["obligations"]["steps"]}, tree.own_steps[leaf])
                check_symbols([inferred[leaf]["requirement"], *inferred[leaf]["steps"].values()], schema)
            except (ValueError, KeyError, TypeError) as error:
                problems.append(f"obligations for {leaf}: {error}")
        for aid, api in answer["apis"].items():
            absent = [key for key in API_KEYS if not isinstance(api, dict) or key not in api]
            if absent:
                problems.append(f"apis.{aid}: missing {absent}; return the complete API record")
                continue
            try:
                check_symbols([api], schema)
            except (ValueError, KeyError, TypeError) as error:
                problems.append(f"apis.{aid}: {error}")
            planned = state["api_plan"].get(aid)
            signature = api.get("signature")
            if planned and isinstance(signature, dict):
                for key in ("method", "path", "name"):
                    if signature.get(key) != planned["signature"].get(key):
                        problems.append(f"apis.{aid}: signature.{key} {signature.get(key)!r} differs from the planned "
                            f"{planned['signature'].get(key)!r}; keep the plan's method, path and name")
        merged = {**state["backend"]["apis"], **answer["apis"]}
        check_api_list(merged, problems)
        for leaf in leaves:
            listed = answer["requirement_apis"][leaf]
            if not isinstance(listed, list):
                problems.append(f"requirement_apis[{leaf}] must be an array of API IDs")
                continue
            for aid in listed:
                if aid not in merged:
                    problems.append(f"requirement_apis[{leaf}]: unknown API {aid}")
        reject(problems)
        obligations = deepcopy(state["obligations"])
        for leaf in leaves:
            obligations["requirements"][leaf] = inferred[leaf]["requirement"]
            obligations["steps"].update(inferred[leaf]["steps"])
        owners = {rid: state["backend"]["requirements"][rid] for rid in state["done"]["data"]}
        owners.update({leaf: answer["requirement_apis"][leaf] for leaf in leaves})
        # Every requirement accepted so far stays covered, also when this group changed a shared API.
        validate_design({"apis": merged, "requirements": owners}, obligations, complete=False, tree=tree)
        return {"merged": merged, "owners": owners, "obligations": obligations}

    accepted, answer = propose_json(model, D_INSTRUCTIONS, request, accept, console,
        "Plan D " + ", ".join(leaves), PLAN_ATTEMPTS)
    updated = deepcopy(state)
    for leaf in leaves:
        updated["schema"]["requirements"][leaf] = deepcopy(answer["requirement_fields"][leaf])
        updated["backend"]["requirements"][leaf] = deepcopy(answer["requirement_apis"][leaf])
        record = accepted["obligations"]["requirements"][leaf]
        updated["conventions"] = merge_conventions(updated["conventions"], answer, leaf, record["read"] + record["write"])
    updated["obligations"] = accepted["obligations"]
    updated["backend"]["apis"] = deepcopy(accepted["merged"])
    updated["done"]["data"] += [leaf for leaf in leaves if leaf not in updated["done"]["data"]]
    return updated


U_OUTPUT = """OUTPUT FORMAT
{"elements": {"ELEMENT-ID": {type, parent, mode, data, prop}},
 "pages": {},
 "requirement_ui": {"REQ-ID": ["ELEMENT-ID"]},
 "step_ui": {"STEP-ID": ["ELEMENT-ID"]}}
Pages and routes are fixed by ui_plan: return pages as {} and attach every element to a
supplied page root or to an element under it. Elements with prop.layout true are the
shared header: use their IDs in requirement_ui and step_ui when a step uses them, but do
not change, repeat or re-create them. ui_plan.naming lists the names the requirements
fix. An element for a listed control uses exactly that name, once per page: label for
links, buttons, fields and regions; text for headings; placeholder with no label when
the listed name is a placeholder. Every link or button you add must be listed in
ui_plan.naming for its page, unless its name is filled in at runtime. Include every
supplied requirement ID and step ID."""

U_INSTRUCTIONS = GROUP_HEADER + "\n\n" + UI_INSTRUCTIONS + "\n\n" + U_OUTPUT


def with_ui_plan(state: dict, ui_plan: dict, tree) -> dict:
    """The state after P2: the UI plan and a design holding page roots and header copies."""
    updated = deepcopy(state)
    updated["ui_plan"] = deepcopy(ui_plan)
    updated["ui"] = ui_skeleton(ui_plan, tree)
    return updated


def call_pages(state: dict, leaves: list[str]) -> list[str]:
    """Pages whose plan lists one of these requirements."""
    return [pid for pid, page in state["ui_plan"]["pages"].items()
        if set(page.get("requirements") or []) & set(leaves) and pid in state["ui"]["pages"]]


def mentions(problem: str, ids) -> bool:
    """True when a problem text names one of the IDs as a whole word (UI-a does not match UI-a-b)."""
    return any(re.search(r"(?<![\w-])" + re.escape(uid) + r"(?![\w-])", problem) for uid in ids)


def ui_worker(state: dict, leaves: list[str], tree, contexts: dict, model, console) -> dict:
    """U: page elements for one group's requirements, inside the pages, header and names of the UI plan."""
    steps = [sid for leaf in leaves for sid in tree.own_steps[leaf]]
    ui, ui_plan = state["ui"], state["ui_plan"]
    pages = call_pages(state, leaves)
    request = {"requirements": [contexts[leaf] for leaf in leaves],
        "ui_plan": {"pages": ui_plan["pages"], "naming": ui_plan["naming"]},
        "pages": {pid: page_context(ui, pid) for pid in pages}}
    roots = {page["root"] for page in ui["pages"].values()}
    header = {uid for uid, node in ui["elements"].items() if (node.get("prop") or {}).get("layout") is True}

    def accept(answer):
        if isinstance(answer, dict) and answer.get("pages") not in (None, {}):
            raise ValueError("Pages are fixed by the UI plan; return pages as {}")
        require_sections(answer, {"elements": [], "requirement_ui": leaves, "step_ui": steps})
        problems = []
        returned = answer["elements"]
        for uid, node in returned.items():
            if uid in roots or uid in header:
                problems.append(f"elements.{uid}: is a page root or a shared header element; do not return it")
            elif not isinstance(node, dict):
                problems.append(f"elements.{uid}: must be an element record")
            elif (node.get("prop") or {}).get("layout"):
                problems.append(f"elements.{uid}: prop.layout is reserved for the shared header")
        reject(problems)
        merged = {**ui["elements"], **returned}
        for uid in returned:
            parent, seen = returned[uid].get("parent"), {uid}
            while parent is not None and parent not in roots:
                if parent not in merged:
                    problems.append(f"elements.{uid}: parent chain reaches unknown element {parent}")
                    break
                if parent in seen:
                    problems.append(f"elements.{uid}: parent cycle")
                    break
                seen.add(parent)
                parent = merged[parent].get("parent")
            else:
                if parent is None:
                    problems.append(f"elements.{uid}: parent must be a supplied page root or an element under it")
        for leaf in leaves:
            listed = answer["requirement_ui"][leaf]
            if not isinstance(listed, list) or not listed:
                problems.append(f"requirement_ui[{leaf}]: list the element IDs this requirement uses")
        for key, listed in [*((leaf, answer["requirement_ui"][leaf]) for leaf in leaves),
                            *((sid, answer["step_ui"][sid]) for sid in steps)]:
            for uid in listed if isinstance(listed, list) else []:
                if uid not in merged:
                    problems.append(f"{key}: unknown element {uid}")
        reject(problems)
        design = ui
        for leaf in leaves:
            design = apply_proposal(design, {"pages": {}, "elements": returned,
                "requirement_ui": answer["requirement_ui"][leaf],
                "step_ui": {sid: answer["step_ui"][sid] for sid in tree.own_steps[leaf]}}, leaf, tree)
        found = []
        images = {iid for page in design["pages"].values() for iid in page.get("images", [])}
        check_ui(design, tree, images, found)
        problems += [problem for problem in found if mentions(problem, returned)]
        finished = set(state["done"]["ui"]) | set(leaves)
        complete = {pid for pid in pages
            if set(ui_plan["pages"][pid].get("requirements") or []) <= finished}
        check_naming(design, ui_plan, problems, frozenset(state["skipped"]), complete=complete)
        reject(problems)
        return design

    design, _ = propose_json(model, U_INSTRUCTIONS, request, accept, console, "Plan U " + ", ".join(leaves), PLAN_ATTEMPTS)
    updated = deepcopy(state)
    updated["ui"] = design
    updated["done"]["ui"] += [leaf for leaf in leaves if leaf not in updated["done"]["ui"]]
    return updated


T_OUTPUT = """OUTPUT FORMAT
{"triggers": {"TRIGGER-ID": {complete trigger record}},
 "requirement_triggers": {"REQ-ID": ["TRIGGER-ID"]},
 "step_triggers": {"STEP-ID": ["TRIGGER-ID"]}}
Include every supplied requirement ID and step ID; use [] where none is needed."""

T_INSTRUCTIONS = GROUP_HEADER + "\n\n" + TRIGGER_INSTRUCTIONS + "\n\n" + T_OUTPUT


def full_obligations(state: dict, tree) -> dict:
    """Normalized records for every requirement and step; a requirement without one (skipped) is empty."""
    raw = state["obligations"]
    return normalize_obligations({
        "requirements": {rid: raw["requirements"].get(rid, EMPTY) for rid in tree.requirements},
        "steps": {sid: raw["steps"].get(sid, EMPTY) for sid in tree.steps}}, tree)


def trigger_worker(state: dict, leaves: list[str], tree, contexts: dict, model, console) -> dict:
    """T: what each click, submit or page load does, for one group's requirements."""
    steps = [sid for leaf in leaves for sid in tree.own_steps[leaf]]
    ui, backend, triggers = state["ui"], state["backend"], state["triggers"]
    obligations = full_obligations(state, tree)
    pages = {}
    for leaf in leaves:
        pages.update(relevant_pages(ui, leaf))
    on_pages = {uid for page in pages.values() for uid in page["elements"]}
    existing = {tid: trigger for tid, trigger in triggers["triggers"].items() if trigger.get("source") in on_pages}
    aids = {aid for leaf in leaves for aid in backend["requirements"].get(leaf, [])}
    aids |= {aid for trigger in existing.values() for aid in api_ids(trigger)}
    request = {"requirements": [{key: value for key, value in contexts[leaf].items() if key != "images"} for leaf in leaves],
        "ui": pages,
        "required_obligations": {leaf: obligations["requirements"][leaf] for leaf in leaves},
        "step_obligations": {sid: obligations["steps"][sid] for sid in steps},
        "existing_triggers": existing,
        "backend_apis": {aid: backend["apis"][aid] for aid in sorted(aids) if aid in backend["apis"]},
        "shared_conventions": select_conventions(state["conventions"], requirements=leaves)}

    def accept(answer):
        require_sections(answer, {"triggers": [], "requirement_triggers": leaves, "step_triggers": steps})
        merged = triggers
        for leaf in leaves:
            merged = apply_decomposition(merged, {"triggers": answer["triggers"],
                "requirement_triggers": answer["requirement_triggers"][leaf],
                "step_triggers": {sid: answer["step_triggers"][sid] for sid in tree.own_steps[leaf]}}, leaf)
        problems = []
        for key, listed in [*((leaf, answer["requirement_triggers"][leaf]) for leaf in leaves),
                            *((sid, answer["step_triggers"][sid]) for sid in steps)]:
            if not isinstance(listed, list):
                problems.append(f"{key}: must be an array of trigger IDs")
                continue
            for tid in listed:
                if tid not in merged["triggers"]:
                    problems.append(f"{key}: unknown trigger {tid}")
        for tid, trigger in answer["triggers"].items():
            where = f"triggers.{tid}"
            if not isinstance(trigger, dict):
                problems.append(f"{where}: must be a trigger record")
                continue
            if trigger.get("source") not in ui["elements"]:
                problems.append(f"{where}: source {trigger.get('source')!r} is not a UI element")
            if not isinstance(trigger.get("action"), list):
                problems.append(f"{where}: action must be an ordered array of api or ui actions")
                continue
            for action in trigger["action"]:
                kind = action.get("kind") if isinstance(action, dict) else None
                if kind == "api" and action.get("api") not in backend["apis"]:
                    problems.append(f"{where}: unknown API {action.get('api')!r}")
                elif kind == "ui" and action.get("ui") not in ui["elements"]:
                    problems.append(f"{where}: unknown UI element {action.get('ui')!r}")
                elif kind not in ("api", "ui"):
                    problems.append(f"{where}: action kind must be api or ui")
        reject(problems)
        covered = [leaf for leaf in [*state["done"]["triggers"], *leaves] if ui["requirements"].get(leaf)]
        validate_coverage(merged, backend, obligations, tree, covered)
        return merged

    merged, _ = propose_json(model, T_INSTRUCTIONS, request, accept, console, "Plan T " + ", ".join(leaves), PLAN_ATTEMPTS)
    updated = deepcopy(state)
    updated["triggers"] = merged
    updated["done"]["triggers"] += [leaf for leaf in leaves if leaf not in updated["done"]["triggers"]]
    return updated


def run_workers(worker, state: dict, groups: list[dict], console, **arguments) -> dict:
    """Run one worker kind over every group part, in order (spec 6, level 1).

    A part that fails is retried once per leaf; a leaf that still fails is skipped: it gets
    empty records later and loses its own tests, and nothing else stops."""
    for part in groups:
        leaves = [leaf for leaf in part["leaves"] if leaf not in state["skipped"]]
        if not leaves:
            continue
        try:
            state = worker(state, leaves, **arguments)
            continue
        except (RuntimeError, ValueError) as error:
            if len(leaves) == 1:
                state = skip(state, leaves[0], error, console)
                continue
        for leaf in leaves:
            try:
                state = worker(state, [leaf], **arguments)
            except (RuntimeError, ValueError) as error:
                state = skip(state, leaf, error, console)
    return state


def skip(state: dict, leaf: str, error: Exception, console) -> dict:
    """Record a leaf whose own call failed; it is left out of every later worker call."""
    console.show(f"Plan | skipped {leaf}: {error}", "Its records stay empty; the other requirements continue.")
    if leaf in state["skipped"]:
        return state
    return {**state, "skipped": [*state["skipped"], leaf]}
