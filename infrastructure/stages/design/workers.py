"""Group workers of the plan stage: data (D), UI (U) and triggers (T).

Each worker designs the details for the requirements of one group in one model call. The
plan state is one dict passed from worker to worker; a worker returns a new dict and never
changes the one it receives, so a failed call leaves the earlier state intact."""

from copy import deepcopy

from infrastructure.pipeline.backend_apis import INSTRUCTIONS as API_INSTRUCTIONS
from infrastructure.pipeline.db_obligations import INSTRUCTIONS as OBLIGATION_INSTRUCTIONS
from infrastructure.shared.agent.conventions import CONVENTION_RULES, merge_conventions
from infrastructure.shared.agent.proposals import propose_json
from infrastructure.stages.backend.primitives import validate_design
from infrastructure.stages.database.obligations.records import check_symbols, normalize_inference
from infrastructure.stages.database.schema import check_related_fields
from infrastructure.stages.design.contract import API_KEYS
from infrastructure.stages.design.planner import PLAN_ATTEMPTS, check_api_list, reject, requirement_records
from infrastructure.stages.frontend.triggers.design import empty_triggers

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
