"""The two whole-app plan calls: P1 (tables and the API list) and P2 (pages, header, naming).

Each call sees every requirement at once, so shared decisions (one table per concept, one
route per page, one name per control) are made once instead of once per requirement."""

from infrastructure.shared.agent.proposals import propose_json
from infrastructure.stages.design.contract import METHODS, check_entities, route_key

PLAN_ATTEMPTS = 3
PLAN_OUTPUT_CAP = 32000

P1_INSTRUCTIONS = """Plan the shared data design for the WHOLE application in one answer. You see every
requirement. Later calls fill in details per requirement group and may not rename
what you define here.

Return one JSON object: {"entities": {...}, "apis": {...}, "conventions": {...}}.

entities maps each entity name to {"description": string, "fields": {...}}. Each field has
- description: string.
- type: string, integer, number, boolean, date, datetime, uuid or json.
- nullable, primary_key, unique: booleans.
- references: null or a qualified "Entity.field" string.
- constraints: array of plain-language value rules taken from the requirements.
Use SQL-compatible names. Every entity has a primary key. Define ONE entity per business
concept and reuse it across requirements; never create a second entity or field for the
same idea. Include data that requirements only read, and state that a Given describes as
already existing. Store persistent business data only, never transient form values.
Passwords are stored as hashes. Do not invent features no requirement mentions.

apis maps API IDs ("API-<verb>-<noun>") to {"description": string, "signature":
{"name": TypeScript function name, "method": GET, POST, PUT, PATCH or DELETE,
"path": "/api/..."}}. List every operation the requirements need, including read
operations for reload, direct entry and reopening a form. One operation per method and
path. Do not write request or response shapes here.

conventions is optional: {"stable-id": "one-sentence decision"} for choices several APIs
share, such as how the current user is identified. Omit it when there is none.

global_rules apply to every requirement (for example the clock, the time zone and the
accounts that exist at start).
"""


def global_rules(tree) -> list[dict]:
    """Descriptions of the root and folder requirements: rules that hold for every requirement."""
    return [{"id": rid, "name": record["name"], "description": record["description"]}
        for rid, record in tree.requirements.items()
        if tree.children[rid] and isinstance(record.get("description"), str) and record["description"].strip()]


def requirement_records(tree, leaves) -> list[dict]:
    """Each leaf requirement with its own scenario steps."""
    return [{**tree.requirements[leaf], "steps": [tree.steps[sid] for sid in tree.own_steps[leaf]]} for leaf in leaves]


def check_api_list(apis, problems, where="apis") -> None:
    """Planned APIs: a description, a valid signature, and one API per method and path."""
    if not isinstance(apis, dict):
        problems.append(f"{where} must be an object of API ID to API record")
        return
    seen = {}
    for aid, api in apis.items():
        signature = api.get("signature") if isinstance(api, dict) else None
        if not isinstance(api, dict) or not isinstance(api.get("description"), str) or not isinstance(signature, dict):
            problems.append(f"{where}.{aid}: needs description and signature")
            continue
        if not isinstance(signature.get("name"), str) or not signature["name"]:
            problems.append(f"{where}.{aid}: signature.name must be a function name")
        if signature.get("method") not in METHODS:
            problems.append(f"{where}.{aid}: method {signature.get('method')!r} is not one of {sorted(METHODS)}")
        path = signature.get("path")
        if not isinstance(path, str) or not path.startswith("/api/"):
            problems.append(f"{where}.{aid}: path {path!r} must start with /api/")
        key = route_key(signature.get("method"), path)
        if key in seen:
            problems.append(f"{where}.{aid}: same method and path as {seen[key]}")
        else:
            seen[key] = aid


def reject(problems: list[str]) -> None:
    """Raise one error that names every problem, so the repair request lists them all."""
    if problems:
        raise ValueError("The plan was rejected:\n- " + "\n- ".join(problems))


def accept_data_plan(answer) -> dict:
    if not isinstance(answer, dict):
        raise ValueError('Return one JSON object {"entities": {...}, "apis": {...}, "conventions": {...}}')
    problems = []
    check_entities(answer.get("entities"), problems)
    check_api_list(answer.get("apis"), problems)
    conventions = answer.get("conventions") or {}
    if not isinstance(conventions, dict) or not all(isinstance(text, str) for text in conventions.values()):
        problems.append('conventions must map a stable ID to a one-sentence decision')
    reject(problems)
    return {"entities": answer["entities"],
        "apis": {aid: {"description": api["description"], "signature": api["signature"]} for aid, api in answer["apis"].items()},
        "conventions": conventions}


def plan_data(tree, model, console) -> dict:
    """P1: every table and every API (ID, description, name, method, path) for the whole app."""
    request = {"global_rules": global_rules(tree), "requirements": requirement_records(tree, tree.leaves)}
    plan, _ = propose_json(model, P1_INSTRUCTIONS, request, accept_data_plan, console, "Plan P1 data", PLAN_ATTEMPTS)
    return plan
