"""The two whole-app plan calls: P1 (tables and the API list) and P2 (pages, header, naming).

Each call sees every requirement at once, so shared decisions (one table per concept, one
route per page, one name per control) are made once instead of once per requirement."""

from copy import deepcopy

from infrastructure.shared.agent.proposals import propose_json
from infrastructure.stages.design.contract import METHODS, check_entities, element_format_problems, route_key
from infrastructure.stages.design.names import ROLES, element_name, element_role
from infrastructure.stages.frontend.ui.design import normalize_design

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


P2_INSTRUCTIONS = """Plan the user interface for the WHOLE application in one answer. Later calls design the
elements of each page and may not change routes, the shared header or names.

Return one JSON object: {"pages": {...}, "layout": {"elements": {...}}, "naming": [...]}.

pages maps PAGE IDs (lower-case letters, digits and underscores) to
{"name", "route", "images", "layout", "requirements"}. route is a URL path starting with
"/"; parameters are written ":name". Use each route once. images lists supplied image IDs;
keep each image's page hint. layout is true when the page shows the shared header.
requirements lists the leaf requirement IDs whose behavior happens on this page. Every
leaf requirement appears on at least one page.

layout.elements is the shared header and navigation, designed once: a map of ELEMENT IDs
("UI-layout-...") to {type, parent, mode, data, prop}, the same format as page elements.
A top-level header element has parent null. Put a control here only when the requirements
say it is available across pages. Return {"elements": {}} when there is no shared header.
Header elements use the page-element format: type is container, link, button, heading,
text, list, image, input or select; mode is an array (user_read and/or user_write, or []);
data is an array of variable declarations {var, dtype, constraints} for values loaded at
runtime, usually []; prop holds fixed text and attributes: label for links, buttons and
fields, text for headings, href for links. Example header link: {"type": "link",
"parent": "UI-layout-nav", "mode": ["user_read"], "data": [], "prop": {"label": "Home",
"href": "/"}}.

naming lists every control the requirement text names:
{"name": exact text, "role": link, button, heading, textbox, combobox, checkbox or region,
 "pages": [PAGE IDs]}.
- Copy names exactly: spelling, case and punctuation.
- A role annotation such as "Save"(role=button) fixes the role. Otherwise choose the role
  the wording implies: something that navigates is a link, something that acts is a button.
- A field the requirement names by its placeholder is listed under that placeholder text.
- A name appears at most once per role on a page. If a header control and a page control
  would share a role and name, keep the header control only.
- Do not list names that are filled in at runtime, such as a record's own title.

Links, buttons and fields come from the requirement text. Reference images decide
arrangement and style only: do not add a control because a screenshot shows one.
"""


def layout_element_id(pid: str, lid: str) -> str:
    """The ID of a header element's copy on one page, for example UI-home-layout-header."""
    return "UI-" + pid + "-" + lid.removeprefix("UI-")


def header_controls(ui_plan: dict) -> list[tuple[str, str, str]]:
    """(header element ID, role, name) for every header element a browser test could look up."""
    regions = {entry.get("name") for entry in ui_plan.get("naming", []) if entry.get("role") == "region"}
    found = []
    for lid, node in ui_plan["layout"]["elements"].items():
        role, name = element_role(node, regions), element_name(node)
        if role and name:
            found.append((lid, role, name))
    return found


def check_ui_plan(answer, tree, images) -> None:
    """P2 accept rules; raises one ValueError that lists every problem."""
    if not isinstance(answer, dict):
        raise ValueError('Return one JSON object {"pages": {...}, "layout": {"elements": {...}}, "naming": [...]}')
    pages, layout, naming = answer.get("pages"), answer.get("layout"), answer.get("naming")
    problems = []
    if not isinstance(pages, dict) or not pages or not all(isinstance(page, dict) for page in pages.values()):
        problems.append("pages must be a non-empty object of PAGE ID to page record")
        pages = {}
    if not isinstance(layout, dict) or not isinstance(layout.get("elements"), dict):
        problems.append('layout must be {"elements": {...}} (use {} when there is no shared header)')
        layout = {"elements": {}}
    if not isinstance(naming, list) or not all(isinstance(entry, dict) for entry in naming):
        problems.append("naming must be an array of {name, role, pages} objects")
        naming = []
    header = layout["elements"]
    routes, placed = {}, set()
    for pid, page in pages.items():
        route = page.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            problems.append(f"pages.{pid}: route {route!r} must start with /")
        elif route in routes:
            problems.append(f"pages.{pid}: same route as {routes[route]} ({route}); use each route once")
        else:
            routes[route] = pid
        for iid in page.get("images") or []:
            if iid not in images:
                problems.append(f"pages.{pid}: unknown image {iid}")
        listed = page.get("requirements")
        if not isinstance(listed, list):
            problems.append(f"pages.{pid}: requirements must be an array of leaf requirement IDs")
            listed = []
        for rid in listed:
            if rid not in tree.leaves:
                problems.append(f"pages.{pid}: {rid} is not a leaf requirement ID")
            placed.add(rid)
        if not isinstance(page.get("layout", False), bool):
            problems.append(f"pages.{pid}: layout must be true or false")
        elif page.get("layout") and not header:
            problems.append(f"pages.{pid}: layout is true but layout.elements is empty; design the shared header "
                "or set layout false")
    for rid in tree.leaves:
        if rid not in placed:
            problems.append(f"requirement {rid} is on no page; list it in the requirements of the page where it happens")
    seen = {}
    for index, entry in enumerate(naming):
        name, role, entry_pages = entry.get("name"), entry.get("role"), entry.get("pages")
        where = f"naming[{index}]"
        if role not in ROLES:
            problems.append(f"{where}: role {role!r} is not one of {list(ROLES)}")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}: name must be the exact non-empty text")
        if not isinstance(entry_pages, list) or not entry_pages:
            problems.append(f"{where}: pages must list the PAGE IDs where {name!r} appears")
            continue
        for pid in entry_pages:
            if pid not in pages:
                problems.append(f"{where}: unknown page {pid!r}")
            key = (role, name, pid)
            if key in seen:
                problems.append(f"{where}: {role} {name!r} is listed twice for page {pid} (also naming[{seen[key]}])")
            seen.setdefault(key, index)
    for lid, node in header.items():
        if not isinstance(node, dict):
            problems.append(f"layout.elements.{lid}: must be an element record")
            continue
        parent = node.get("parent")
        if parent is not None and parent not in header:
            problems.append(f"layout.elements.{lid}: parent must be null or another layout element, not {parent!r}")
        problems.extend(element_format_problems(f"layout.elements.{lid}", node))
        label = (node.get("prop") or {}).get("label") if isinstance(node.get("prop"), dict) else None
        if node.get("type") in ("link", "button") and (not isinstance(label, str) or not label.strip()):
            problems.append(f"layout.elements.{lid}: a header link or button needs its visible name in prop.label")
    reject(problems)


def plan_ui(tree, contexts: dict, images: dict, data_plan: dict, model, console) -> dict:
    """P2: pages and routes, the shared header (designed once) and the naming table."""
    request = {"global_rules": global_rules(tree),
        "requirements": [contexts[leaf] for leaf in tree.leaves],
        "images": {iid: {"page": image.get("page"), "analysis": image.get("analysis", {})} for iid, image in images.items()},
        "apis": {aid: {"description": api["description"], "signature": api["signature"]}
            for aid, api in data_plan["apis"].items()}}

    def accept(answer):
        check_ui_plan(answer, tree, images)
        return {"pages": answer["pages"], "layout": answer["layout"], "naming": answer["naming"]}

    ui_plan, _ = propose_json(model, P2_INSTRUCTIONS, request, accept, console, "Plan P2 UI", PLAN_ATTEMPTS)
    ui_plan = deepcopy(ui_plan)
    header_pages = [pid for pid, page in ui_plan["pages"].items() if page.get("layout") is True]
    # Every header link or button is listed for every header page. An entry the planner already wrote
    # for it is kept: the header copy is that control on the page. A page's own second copy is rejected
    # later by the UI worker's naming check and by the stage 08 strict test.
    for _, role, name in header_controls(ui_plan):
        if role not in ("link", "button"):
            continue
        covered = {pid for entry in ui_plan["naming"] if (entry["role"], entry["name"]) == (role, name)
            for pid in entry["pages"]}
        missing = [pid for pid in header_pages if pid not in covered]
        if missing:
            ui_plan["naming"].append({"name": name, "role": role, "pages": missing})
    return ui_plan


def ui_skeleton(ui_plan: dict, tree) -> dict:
    """A UI design holding every page root and, on header pages, a copy of the shared header."""
    design = {"pages": {}, "elements": {}, "requirements": {rid: [] for rid in tree.requirements},
        "steps": {sid: [] for sid in tree.steps}}
    header = ui_plan.get("layout", {}).get("elements", {})
    for pid, page in ui_plan["pages"].items():
        root = f"UI-{pid}"
        design["pages"][pid] = {"name": page.get("name", pid), "route": page["route"], "root": root,
            "images": list(page.get("images") or [])}
        design["elements"][root] = {"type": "page", "parent": None, "mode": [], "data": [], "prop": {}}
        if page.get("layout") is True:
            for lid, node in header.items():
                copy = deepcopy(node)
                parent = node.get("parent")
                copy["parent"] = root if parent is None else layout_element_id(pid, parent)
                copy["prop"] = {**(copy.get("prop") or {}), "layout": True}
                design["elements"][layout_element_id(pid, lid)] = copy
    return normalize_design(design, tree)
