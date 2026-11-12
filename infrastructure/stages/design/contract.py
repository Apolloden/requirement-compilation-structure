"""Design contract check: the five design files against everything the build stages read.

Checks db_schema, db_obligations, backend_apis, ui_design and frontend_triggers against what
stages 04, 06, 08 and 10 rely on. It reuses the agent's own validators and adds the checks
that are missing upstream (missing records crash later stages instead of failing early).

Returns a list of problems in plain words; [] means the files are safe to hand to the build
stages. It never stops at the first problem, so one repair request can list them all.
"""

import json
import re
from pathlib import Path

from infrastructure.stages.requirements.tree import RequirementTree
from infrastructure.stages.database.schema import check_related_fields
from infrastructure.stages.database.order import implementation_groups
from infrastructure.stages.database.obligations.records import (check_symbols, normalize_inference,
    normalize_obligations)
from infrastructure.stages.backend.primitives import validate_design
from infrastructure.stages.frontend.triggers.design import validate_coverage
from infrastructure.stages.design.names import named_elements, page_subtree

FIELD_TYPES = {"string", "integer", "number", "boolean", "date", "datetime", "uuid", "json"}
DTYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
API_KEYS = ("description", "read", "write", "signature", "request", "response", "precondition", "postcondition")
PAGE_TYPES = {"page"}


def image_ids(node):
    """Image IDs declared anywhere in the requirement tree (description.image[].id)."""
    found = set()
    if isinstance(node, dict):
        description = node.get("description")
        if isinstance(description, dict):
            found |= {image["id"] for image in description.get("image", []) or [] if isinstance(image, dict) and "id" in image}
        for value in node.values():
            found |= image_ids(value)
    elif isinstance(node, list):
        for value in node:
            found |= image_ids(value)
    return found


def guarded(problems, label, check):
    """Run one existing validator; record its message instead of stopping."""
    try:
        check()
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        problems.append(f"{label}: {type(error).__name__}: {error}")


def check_entities(entities, problems):
    """Entity and field rules: SQL names, field types and flags, references, a primary key, table order."""
    if not isinstance(entities, dict) or not entities:
        problems.append("db_schema: entities must be a non-empty object")
        return False
    symbols = {f"{entity}.{field}" for entity, definition in entities.items() if isinstance(definition, dict)
        for field in (definition.get("fields") or {})}
    for entity, definition in entities.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entity):
            problems.append(f"db_schema: entity name {entity!r} is not an SQL identifier")
        fields = definition.get("fields") if isinstance(definition, dict) else None
        if not isinstance(fields, dict) or not fields:
            problems.append(f"db_schema: {entity} has no fields")
            continue
        if not any(isinstance(field, dict) and field.get("primary_key") is True for field in fields.values()):
            problems.append(f"db_schema: {entity} has no primary key")
        for name, field in fields.items():
            where = f"db_schema: {entity}.{name}"
            if not isinstance(field, dict):
                problems.append(f"{where}: field must be an object")
                continue
            if field.get("type") not in FIELD_TYPES:
                problems.append(f"{where}: type {field.get('type')!r} is not one of {sorted(FIELD_TYPES)}")
            for flag in ("nullable", "primary_key", "unique"):
                if not isinstance(field.get(flag), bool):
                    problems.append(f"{where}: {flag} must be true or false")
            reference = field.get("references")
            if reference is not None and reference not in symbols:
                problems.append(f"{where}: references unknown field {reference!r}")
            if not isinstance(field.get("constraints", []), list):
                problems.append(f"{where}: constraints must be an array")
    guarded(problems, "db_schema: table order", lambda: implementation_groups({"entities": entities}))
    return True


def check_schema(schema, tree, problems):
    if not check_entities(schema.get("entities"), problems):
        return
    for rid in tree.leaves:
        guarded(problems, f"db_schema.requirements[{rid}]",
            lambda: check_related_fields(schema, schema.get("requirements", {})[rid]))


def route_key(method, path):
    """Method and path with path parameters made equal, so /books/{id} and /books/:bookId clash."""
    return (method, re.sub(r"\{[^}]+\}|:[A-Za-z_]+", "{}", str(path)))


def check_obligations(raw, schema, tree, problems):
    """Report missing or malformed records; return normalized records (missing ones empty)."""
    requirements = raw.get("requirements") if isinstance(raw, dict) else None
    steps = raw.get("steps") if isinstance(raw, dict) else None
    requirements = dict(requirements) if isinstance(requirements, dict) else {}
    steps = dict(steps) if isinstance(steps, dict) else {}
    missing = [rid for rid in tree.leaves if rid not in requirements]
    if missing:
        problems.append("db_obligations: no record for leaf requirements " + ", ".join(missing))
    # Fill gaps so the remaining checks still run; the gap itself is reported above.
    for rid in tree.requirements:
        requirements.setdefault(rid, {"read": [], "write": []})
    for sid in tree.steps:
        steps.setdefault(sid, {"read": [], "write": []})
    try:
        obligations = normalize_obligations({"requirements": requirements, "steps": steps}, tree)
    except (ValueError, KeyError, TypeError) as error:
        problems.append(f"db_obligations: {type(error).__name__}: {error}")
        empty = {"read": [], "write": []}
        obligations = normalize_obligations({"requirements": {rid: empty for rid in tree.requirements},
            "steps": {sid: empty for sid in tree.steps}}, tree)
    guarded(problems, "db_obligations: symbols", lambda: check_symbols(
        [*obligations["requirements"].values(), *obligations["steps"].values()], schema))
    for rid in tree.leaves:
        guarded(problems, f"db_obligations[{rid}]: step effects outside the requirement", lambda rid=rid: normalize_inference(
            {"requirement": obligations["requirements"][rid],
             "steps": {sid: obligations["steps"][sid] for sid in tree.own_steps[rid]}}, tree.own_steps[rid]))
    return obligations


def check_backend(backend, schema, obligations, tree, problems):
    apis, owners = backend.get("apis"), backend.get("requirements")
    if not isinstance(apis, dict) or not isinstance(owners, dict):
        problems.append("backend_apis: needs apis and requirements objects")
        return
    seen = {}
    for aid, api in apis.items():
        absent = [key for key in API_KEYS if key not in api]
        if absent:
            problems.append(f"backend_apis.{aid}: missing {absent}")
            continue
        signature = api["signature"]
        method, path = signature.get("method"), signature.get("path")
        if method not in METHODS:
            problems.append(f"backend_apis.{aid}: method {method!r} is not one of {sorted(METHODS)}")
        if not isinstance(path, str) or not path.startswith("/api/"):
            problems.append(f"backend_apis.{aid}: path {path!r} must start with /api/")
        key = route_key(method, path)
        if key in seen:
            problems.append(f"backend_apis.{aid}: same method and path as {seen[key]}")
        seen[key] = aid
    for rid in tree.leaves:
        if rid not in owners:
            problems.append(f"backend_apis.requirements: no entry for {rid}")
        for aid in owners.get(rid, []):
            if aid not in apis:
                problems.append(f"backend_apis.requirements[{rid}]: unknown API {aid}")
    guarded(problems, "backend_apis: symbols", lambda: check_symbols(apis.values(), schema))
    guarded(problems, "backend_apis: obligation coverage",
        lambda: validate_design(backend, obligations, complete=True, tree=tree))


def check_ui(ui, tree, images, problems, skipped=frozenset()):
    if not all(isinstance(ui.get(key), dict) for key in ("pages", "elements", "requirements", "steps")):
        problems.append("ui_design: needs pages, elements, requirements and steps objects")
        return
    pages, elements = ui["pages"], ui["elements"]
    if not pages:
        problems.append("ui_design: no pages")
    routes = {}
    for pid, page in pages.items():
        root, route = page.get("root"), page.get("route")
        if root not in elements:
            problems.append(f"ui_design.pages.{pid}: root {root!r} is not an element")
        elif elements[root].get("parent") is not None:
            problems.append(f"ui_design.pages.{pid}: root {root} must have parent null")
        if not isinstance(route, str) or not route.startswith("/"):
            problems.append(f"ui_design.pages.{pid}: route {route!r} must start with /")
        elif route in routes:
            problems.append(f"ui_design.pages.{pid}: same route as {routes[route]}")
        else:
            routes[route] = pid
        for iid in page.get("images", []):
            if iid not in images:
                problems.append(f"ui_design.pages.{pid}: unknown image {iid}")
    roots = {page.get("root") for page in pages.values()}
    for uid, node in elements.items():
        where = f"ui_design.elements.{uid}"
        if not isinstance(node.get("type"), str):
            problems.append(f"{where}: type missing")
        parent, seen = node.get("parent"), {uid}
        if parent is None and uid not in roots:
            problems.append(f"{where}: parent null but it is not a page root")
        while parent is not None:
            if parent not in elements:
                problems.append(f"{where}: parent chain reaches unknown element {parent}")
                break
            if parent in seen:
                problems.append(f"{where}: parent cycle")
                break
            seen.add(parent)
            parent = elements[parent].get("parent")
        for declaration in node.get("data", []):
            if not isinstance(declaration, dict) or not isinstance(declaration.get("var"), str):
                problems.append(f"{where}: data entries need a var name")
            elif declaration.get("dtype") not in DTYPES:
                problems.append(f"{where}: dtype {declaration.get('dtype')!r} is not one of {sorted(DTYPES)}")
        visible = (node.get("prop") or {}).get("visible", True)
        if not (isinstance(visible, bool) or (isinstance(visible, dict) and {"var", "equals"} <= set(visible))):
            problems.append(f"{where}: prop.visible must be true/false or {{var, equals}}")
    for kind, expected in (("requirements", tree.requirements), ("steps", tree.steps)):
        for key in expected:
            for uid in ui[kind].get(key, []):
                if uid not in elements:
                    problems.append(f"ui_design.{kind}[{key}]: unknown element {uid}")
    for rid in tree.leaves:
        if rid not in skipped and not ui["requirements"].get(rid):
            problems.append(f"ui_design.requirements[{rid}]: no UI elements (stage 10 would skip it)")


def check_duplicate_controls(ui, problems):
    """Without a naming table: two links or buttons with the same name on one page."""
    # Browser tests look controls up strictly: two links or buttons with the same name on one
    # page fail every test that clicks them.
    pages, elements = ui.get("pages", {}), ui.get("elements", {})
    for pid, page in pages.items():
        named = {}
        for uid, node in elements.items():
            label = (node.get("prop") or {}).get("label")
            if node.get("type") in {"link", "button"} and isinstance(label, str) and "{" not in label:
                current, seen = uid, set()
                while current in elements and current not in seen:
                    seen.add(current)
                    if current == page.get("root"):
                        named.setdefault((node["type"], label), []).append(uid)
                        break
                    current = elements[current].get("parent")
        for (kind, label), uids in named.items():
            if len(uids) > 1:
                problems.append(f"ui_design.pages.{pid}: {len(uids)} {kind}s named {label!r} ({', '.join(uids)}); "
                    "strict browser tests need one")


def check_naming(ui, plan, problems, skipped=frozenset(), complete=None):
    """With a naming table: every listed control exists exactly once, with exactly its name, on
    each page that lists it; every fixed-name link or button is listed; header pages hold the header.

    complete limits the "missing control" report to those page IDs (None: every page); the plan
    workers use it while pages are still being filled in."""
    if not all(isinstance(ui.get(key), dict) for key in ("pages", "elements")):
        return
    plan_pages = plan.get("pages") or {}
    naming = [entry for entry in plan.get("naming") or [] if isinstance(entry, dict)]
    for pid in ui["pages"]:
        page_plan = plan_pages.get(pid) or {}
        found = named_elements(ui, pid, plan)
        listed = [entry for entry in naming if pid in (entry.get("pages") or [])]
        exempt = any(rid in skipped for rid in page_plan.get("requirements") or []) or (
            complete is not None and pid not in complete)
        for entry in listed:
            role, name = entry.get("role"), entry.get("name")
            uids = [uid for uid, found_role, found_name in found if (found_role, found_name) == (role, name)]
            if not uids and not exempt:
                problems.append(f"ui_design.pages.{pid}: no {role} named {name!r} (naming table)")
            elif len(uids) > 1:
                problems.append(f"ui_design.pages.{pid}: {len(uids)} {role}s named {name!r} ({', '.join(uids)}); "
                    "strict browser tests need one")
        known = {(entry.get("role"), entry.get("name")) for entry in listed}
        for uid, role, name in found:
            if role in ("link", "button") and (role, name) not in known:
                problems.append(f"ui_design.pages.{pid}: {role} {name!r} ({uid}) is not in the naming table")
        if page_plan.get("layout") and not any((ui["elements"][uid].get("prop") or {}).get("layout") is True
                for uid in page_subtree(ui, pid)):
            problems.append(f"ui_design.pages.{pid}: page shows the header but has no header elements")


def check_triggers(design, ui, backend, obligations, tree, problems):
    if not all(isinstance(design.get(key), dict) for key in ("triggers", "requirements", "steps")):
        problems.append("frontend_triggers: needs triggers, requirements and steps objects")
        return
    triggers, elements, apis = design["triggers"], ui.get("elements", {}), backend.get("apis", {})
    for kind, expected in (("requirements", tree.requirements), ("steps", tree.steps)):
        for key in expected:
            if key not in design[kind]:
                problems.append(f"frontend_triggers.{kind}: no entry for {key} (stage 10 crashes on a missing leaf)")
            for tid in design[kind].get(key, []):
                if tid not in triggers:
                    problems.append(f"frontend_triggers.{kind}[{key}]: unknown trigger {tid} (stage 10 crashes)")
    for tid, trigger in triggers.items():
        where = f"frontend_triggers.triggers.{tid}"
        if trigger.get("source") not in elements:
            problems.append(f"{where}: source {trigger.get('source')!r} is not a UI element")
        if not isinstance(trigger.get("action"), list):
            problems.append(f"{where}: action must be an array")
            continue
        for action in trigger["action"]:
            kind = action.get("kind") if isinstance(action, dict) else None
            if kind == "api" and action.get("api") not in apis:
                problems.append(f"{where}: unknown API {action.get('api')!r}")
            elif kind == "ui" and action.get("ui") not in elements:
                problems.append(f"{where}: unknown UI element {action.get('ui')!r}")
            elif kind not in {"api", "ui"}:
                problems.append(f"{where}: action kind must be api or ui")
    if not any(problem.startswith("frontend_triggers") for problem in problems):
        guarded(problems, "frontend_triggers: obligation coverage",
            lambda: validate_coverage(design, backend, obligations, tree, tree.leaves))


def check_artifacts(requirements: dict, schema: dict, obligations: dict, backend: dict, ui: dict,
                    triggers: dict, plan: dict | None = None, skipped: frozenset = frozenset()) -> list[str]:
    """All problems with the design files; [] means safe to build.

    obligations is the raw db_obligations content. skipped holds leaf IDs that were emptied on
    purpose: for them "no UI elements" is not a problem."""
    tree = RequirementTree(requirements)
    skipped = frozenset(skipped)
    problems = []
    check_schema(schema, tree, problems)
    normalized = check_obligations(obligations, schema, tree, problems)
    check_backend(backend, schema, normalized, tree, problems)
    check_ui(ui, tree, image_ids(requirements), problems, skipped)
    if plan is None:
        check_duplicate_controls(ui, problems)
    else:
        check_naming(ui, plan, problems, skipped)
    check_triggers(triggers, ui, backend, normalized, tree, problems)
    return problems


def check_design(arc: Path) -> list[str]:
    """Load the design files from arc (and ui_plan.json when present) and check them."""
    def load(name):
        return json.loads((arc / name).read_text(encoding="utf-8"))
    plan = load("ui_plan.json") if (arc / "ui_plan.json").is_file() else None
    return check_artifacts(load("requirements.json"), load("db_schema.json"), load("db_obligations.json"),
        load("backend_apis.json"), load("ui_design.json"), load("frontend_triggers.json"), plan=plan)
