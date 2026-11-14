"""The plan: one JSON object that fixes tables, API, layout, pages and modules before any code is written."""
from __future__ import annotations

import json
import re

from arcflash import config
from arcflash.prompting import build_context, fill, load_prompt
from arcflash.spec import Spec

SNAKE = re.compile(r"[a-z][a-z0-9_]*")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Interactive roles a grader looks up only when the requirements state them (evidence.md C2: unstated menuitems).
STATED_ONLY_ROLES = frozenset({"menuitem", "menuitemcheckbox", "menuitemradio", "option", "tab", "switch", "treeitem"})


class PlanError(RuntimeError):
    pass


def page_component(page_id: str) -> str:
    """Component name of a page: "item_detail" → "PageItemDetail"."""
    return "Page" + "".join(part[:1].upper() + part[1:] for part in page_id.split("_") if part)


def _dicts(items) -> list[dict]:
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _shape_problems(plan: dict) -> list[str]:
    problems = [f"plan.{key} must be an object" for key in ("app", "layout") if not isinstance(plan.get(key), dict)]
    problems += [f"plan.{key} must be a list" for key in ("tables", "api", "pages") if not isinstance(plan.get(key), list)]
    if isinstance(plan.get("layout"), dict) and not isinstance(plan["layout"].get("controls"), list):
        problems.append("plan.layout.controls must be a list")
    return problems


SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")


def _role_stated(spec: Spec, role: str, name: str) -> bool:
    """"Name"(role=x), or the role word in the sentence that has the name or in the sentence before it
    ("the Save menuitem", "Save (menuitem)", "these menuitems:\\n- Save")."""
    if (role, name) in spec.role_names:
        return True
    word = re.compile(rf"\b{re.escape(role)}s?\b")
    sentences = SENTENCE_END.split(spec.corpus)
    return any(name in s and (word.search(s) or (i and word.search(sentences[i - 1]))) for i, s in enumerate(sentences))


def _control_problems(where: str, controls: list[dict], spec: Spec, notes_text: str) -> list[str]:
    problems = []
    for c in controls:
        role, name = c.get("role"), str(c.get("name", ""))
        if role not in config.ARIA_ROLES:
            problems.append(f'{where}: role "{role}" is not an ARIA role')
        if "{" in name:
            problems.append(f'{where}: control name "{name}" is filled in at runtime; describe it in "shows"')
        elif name == "":
            continue   # an element the text knows only by its role, such as an error with role=alert
        elif not name.strip() or (name not in spec.corpus and name not in notes_text):
            problems.append(f'{where}: control name "{name}" is not in the requirements; check the wording')
        if role in STATED_ONLY_ROLES and name.strip() and not _role_stated(spec, role, name):
            problems.append(f'{where}: {role} "{name}" has a role the requirements do not state; '
                            'an action they name without a role is a link or a button')
    return problems


def _pairs(controls: list[dict]) -> list[tuple[str, str]]:
    return [(c.get("role"), c.get("name")) for c in controls]


def _triples(controls: list[dict]) -> list[tuple]:
    return [(c.get("role"), c.get("name"), str(c.get("when", ""))) for c in controls]


RUNTIME = re.compile(r"\{|<[^<>]+>")


def _runtime(name: str, spec: Spec) -> bool:
    """A name filled in at runtime: a {template} always (the input format writes placeholders so), a <value> unless the
    requirement text has it literally (Review Focus 4)."""
    return "{" in name or (bool(RUNTIME.search(name)) and name not in spec.corpus)


def validate_plan(plan: dict, spec: Spec, notes_text: str = "") -> list[str]:
    """Every rule of Task 8, with its exact problem text. An empty list means the plan is usable as it is."""
    problems = _shape_problems(plan)
    if problems:
        return problems
    pages, apis, modules, tables = _dicts(plan["pages"]), _dicts(plan["api"]), _dicts(plan.get("modules", [])), _dicts(plan["tables"])
    layout_controls = _dicts(plan["layout"]["controls"])

    # 2-3: page ids and routes
    names_of = {}
    seen_ids, seen_routes = set(), set()
    for n, page in enumerate(pages, start=1):
        pid = page.get("id")
        if not isinstance(pid, str) or not SNAKE.fullmatch(pid):
            problems.append(f"page {n} needs an id in lowercase snake_case")
            names_of[n] = f"page {n}"
            continue
        names_of[n] = f"page {pid}"
        if pid in seen_ids:
            problems.append(f'pages: id "{pid}" is used twice')
        seen_ids.add(pid)
    for n, page in enumerate(pages, start=1):
        route = page.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            problems.append(f'{names_of[n]}: route must start with "/"')
        elif route in seen_routes:
            problems.append(f'pages: route "{route}" is used twice')
        else:
            seen_routes.add(route)

    # 4: requirements
    covered = set()
    for n, page in enumerate(pages, start=1):
        for req in page.get("reqs", []) if isinstance(page.get("reqs"), list) else []:
            if req not in spec.requirements:
                problems.append(f"{names_of[n]}: unknown requirement {req}")
            covered.add(req)
    problems += [f"requirement {leaf} is on no page" for leaf in spec.leaves if leaf not in covered]

    # 5-7: roles, names and duplicates
    problems += _control_problems("layout", layout_controls, spec, notes_text)
    # The same role and name with another "when" is allowed (a per-row action, a second state); only exact repeats count.
    # A page control with a layout control's name is allowed too (a form's submit button); settle_plan notes it.
    layout_pairs = _pairs(layout_controls)
    layout_triples = _triples(layout_controls)
    for t in dict.fromkeys(t for t in layout_triples if layout_triples.count(t) > 1):
        problems.append(f'layout: {t[0]} "{t[1]}" appears twice')
    for n, page in enumerate(pages, start=1):
        controls = _dicts(page.get("controls", []))
        problems += _control_problems(names_of[n], controls, spec, notes_text)
        triples = _triples(controls)
        for t in dict.fromkeys(t for t in triples if triples.count(t) > 1):
            problems.append(f'{names_of[n]}: {t[0]} "{t[1]}" appears twice')

    # 8: every "Name"(role=x) of the text is planned (runtime values such as "<created title>" excepted)
    planned = set(layout_pairs) | {pair for page in pages for pair in _pairs(_dicts(page.get("controls", [])))}
    problems += [f'the requirements name {role} "{name}", but no page or layout control has it'
                 for role, name in spec.role_names if (role, name) not in planned and not RUNTIME.search(name)]

    # 9-10: api and accounts
    seen_api = set()
    for n, api in enumerate(apis, start=1):
        method, path = str(api.get("method", "")).upper(), str(api.get("path", ""))
        if method not in METHODS:
            problems.append(f"api {n}: method must be GET, POST, PUT, PATCH or DELETE")
        if not path.startswith("/api/"):
            problems.append(f"api {method} {path}: path must start with /api/")
        key = (method, path)
        if key in seen_api:
            problems.append(f"api: {method} {path} is listed twice")
        seen_api.add(key)
    auth = plan.get("auth") if isinstance(plan.get("auth"), dict) else {}
    if auth.get("accounts") is True and ("GET", "/api/session") not in seen_api:
        problems.append("auth.accounts is true but api has no GET /api/session")

    # 11: modules
    module_ids = []
    membership: dict[str, list[str]] = {}
    for module in modules:
        mid = module.get("id")
        if not isinstance(mid, str) or not SNAKE.fullmatch(mid):
            problems.append(f'module id "{mid}" must be lowercase snake_case')
        module_ids.append(mid)
        listed = module.get("pages", []) if isinstance(module.get("pages"), list) else []
        if len(listed) > config.MAX_PAGES_PER_MODULE:
            problems.append(f"module {mid} has {len(listed)} pages; at most {config.MAX_PAGES_PER_MODULE}")
        for pid in listed:
            membership.setdefault(pid, []).append(mid)
    for n, page in enumerate(pages, start=1):
        pid, module = page.get("id"), page.get("module")
        if module not in module_ids:
            problems.append(f'{names_of[n]}: module "{module}" does not exist')
        elif membership.get(pid) and module not in membership[pid]:
            problems.append(f'{names_of[n]}: module "{module}" does not list it')
        if not membership.get(pid):
            problems.append(f"modules: page {pid} is in no module")
        elif len(membership[pid]) > 1:
            problems.append(f"modules: page {pid} is in more than one module")

    # 12: tables
    seen_tables = set()
    for table in tables:
        name = table.get("name")
        if name in seen_tables:
            problems.append(f'tables: name "{name}" is used twice')
        seen_tables.add(name)
        if not isinstance(table.get("columns"), list) or not table["columns"]:
            problems.append(f"table {name} needs at least one column")

    return list(dict.fromkeys(problems))


def _parse(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"the top level is a {type(data).__name__}, not an object")
    return data


TOP_GROUP = re.compile(r"^(REQ-\d+)")


def _page_group(page: dict) -> str | None:
    """The top-level requirement group (REQ-8 for REQ-8.3.1) most of the page's reqs belong to; ties go to the first."""
    groups = [m.group(1) for r in page.get("reqs", []) if isinstance(r, str) and (m := TOP_GROUP.match(r))]
    return max(dict.fromkeys(groups), key=groups.count) if groups else None


def settle_modules(plan: dict) -> None:
    """Modules by requirement group, in place and without a model call (Task 8). A flow (booking, then payment) lives
    in one group of the input format, so it stays in one module, written by one call. Groups in order of first
    appearance, the home page's group first; a page without reqs follows the page before it; a group over
    MAX_PAGES_PER_MODULE is split in list order; a module of 1 or 2 pages joins the module before it when the total
    stays within the limit. Ids m1, m2, ... Writes plan["modules"] and every page["module"]."""
    if not isinstance(plan.get("pages"), list):
        return
    pages = [p for p in _dicts(plan["pages"]) if isinstance(p.get("id"), str)]
    if not pages:
        plan["modules"] = []
        return
    home = next((p for p in pages if p.get("route") == "/"), pages[0])
    groups: dict[str | None, list[str]] = {}
    previous = _page_group(home)
    for page in pages:
        group = _page_group(page)
        group = previous if group is None else group
        previous = group
        groups.setdefault(group, [])
    home_group = _page_group(home) if _page_group(home) is not None else next(iter(groups))
    order = [home_group] + [g for g in groups if g != home_group]
    previous = _page_group(home)
    members: dict[str | None, list[str]] = {g: [] for g in order}
    for page in pages:
        group = _page_group(page)
        group = previous if group is None else group
        previous = group
        members[group].append(page["id"])
    size = config.MAX_PAGES_PER_MODULE
    chunks = [members[g][i:i + size] for g in order for i in range(0, len(members[g]), size) if members[g]]
    merged: list[list[str]] = []
    for chunk in chunks:
        if merged and len(chunk) <= 2 and len(merged[-1]) + len(chunk) <= size:
            merged[-1].extend(chunk)
        else:
            merged.append(list(chunk))
    plan["modules"] = [{"id": f"m{n}", "pages": chunk} for n, chunk in enumerate(merged, 1)]
    owner = {pid: f"m{n}" for n, chunk in enumerate(merged, 1) for pid in chunk}
    for page in pages:
        page["module"] = owner[page["id"]]


def settle_areas(plan: dict) -> list[str]:
    """Keep a control's "in" only when it names an area of the same page or of the layout (Task 6b); drop the rest,
    in place, and return a note for each."""
    notes = []
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    layout_controls = _dicts(layout.get("controls", []))
    layout_names = {c.get("name") for c in layout_controls}
    owners = [("layout", layout_controls, set())] + [(f"page {p.get('id')}", _dicts(p.get("controls", [])), None)
                                                     for p in _dicts(plan.get("pages", []))]
    for where, controls, _ in owners:
        names = layout_names | {c.get("name") for c in controls}
        for c in controls:
            if "in" in c and (not isinstance(c["in"], str) or c["in"] not in names or c["in"] == c.get("name")):
                notes.append(f'{where}: {c.get("role")} "{c.get("name")}" was placed in "{c["in"]}", which is no area of '
                             'that page or the layout')
                del c["in"]
    return notes


def _ancestors(spec: Spec, req: str) -> list[str]:
    chain, seen = [], set()
    parent = spec.requirements[req].parent if req in spec.requirements else None
    while parent and parent in spec.requirements and parent not in seen:
        chain.append(parent); seen.add(parent)
        parent = spec.requirements[parent].parent
    return chain


def _home(pages: list[dict]) -> dict | None:
    return next((p for p in pages if p.get("route") == "/"), pages[0] if pages else None)


def _group_page(pages: list[dict], spec: Spec, req: str, holders: list[dict] | None = None) -> dict | None:
    """The page (among `holders`, default all) that holds most requirements of req's nearest group; ties go to the first."""
    candidates = holders if holders is not None else pages
    for group in _ancestors(spec, req):
        counts = [sum(group in _ancestors(spec, r) for r in p.get("reqs", []) if isinstance(r, str)) for p in candidates]
        if counts and max(counts) > 0:
            return candidates[counts.index(max(counts))]
    return candidates[0] if holders else _home(pages)


def settle_plan(plan: dict, spec: Spec, notes_text: str = "") -> list[str]:
    """Repair the plan in place by code (Task 7; no plan-fix call) and return what code could not repair, as short notes
    for the code calls. A plan whose shape is wrong returns only its shape problems (the caller asks once more)."""
    shape = _shape_problems(plan)
    if shape:
        return shape
    pages, layout_controls = _dicts(plan["pages"]), _dicts(plan["layout"]["controls"])
    notes: list[str] = []

    # ids, routes and modules that only need lowercase or a leading slash
    for page in pages:
        if isinstance(page.get("id"), str):
            page["id"] = re.sub(r"[^a-z0-9_]+", "_", page["id"].strip().lower()).strip("_") or page["id"]
        if isinstance(page.get("route"), str) and page["route"] and not page["route"].startswith("/"):
            page["route"] = "/" + page["route"]

    for where, controls, page in [("layout", layout_controls, None)] + [(f"page {p.get('id')}", _dicts(p.get("controls", [])), p) for p in pages]:
        kept, seen = [], set()
        for c in controls:
            role, name = c.get("role"), str(c.get("name", ""))
            # a name filled in at runtime is something the page shows, not a fixed control
            if name and _runtime(name, spec):
                if page is not None:
                    page.setdefault("shows", []).append(f'{role} "{name}"')
                continue
            # an action named without a role is a link or a button (evidence C2)
            if role in STATED_ONLY_ROLES and name.strip() and not _role_stated(spec, role, name):
                c["role"] = role = "link" if c.get("to") else "button"
            key = (role, name, str(c.get("when", "")))
            if key in seen:   # an exact duplicate
                continue
            seen.add(key)
            kept.append(c)
        if page is None:
            plan["layout"]["controls"] = kept
            layout_controls = kept
        else:
            page["controls"] = kept

    # a requirement on no page joins the page that holds most requirements of its group
    covered = {r for p in pages for r in p.get("reqs", []) if isinstance(p.get("reqs"), list)}
    for leaf in spec.leaves:
        if leaf not in covered and pages:
            target = _group_page(pages, spec, leaf)
            if not isinstance(target.get("reqs"), list):
                target["reqs"] = []
            target["reqs"].append(leaf)

    # a "Name"(role=x) that no page or layout plans goes to the page of the requirement that names it
    planned = set(_pairs(layout_controls)) | {pair for p in pages for pair in _pairs(_dicts(p.get("controls", [])))}
    for role, name in spec.role_names:
        if (role, name) in planned or RUNTIME.search(name):
            continue
        marker = f'"{name}"(role={role})'
        owner = next((r for r in spec.leaves if marker in spec.requirements[r].text), None) \
            or next((r for r in spec.leaves if name in spec.requirements[r].text), None)
        holders = [p for p in pages if owner in p.get("reqs", [])] if owner else []
        target = _group_page(pages, spec, owner, holders) if holders else _home(pages)
        if target is not None:
            target.setdefault("controls", []).append({"role": role, "name": name, "when": "always"})
            planned.add((role, name))

    # api: upper-case methods, no exact repeats, a session route when there are accounts
    apis, seen_api = [], set()
    for api in _dicts(plan["api"]):
        api["method"] = str(api.get("method", "")).upper()
        key = (api["method"], api.get("path"))
        if key not in seen_api:
            seen_api.add(key); apis.append(api)
    auth = plan.get("auth") if isinstance(plan.get("auth"), dict) else {}
    if auth.get("accounts") is True and ("GET", "/api/session") not in seen_api:
        apis.append({"method": "GET", "path": "/api/session", "request": {}, "response": '{"user": null}', "errors": [], "reqs": []})
    plan["api"] = apis

    settle_modules(plan)
    notes += settle_areas(plan)

    # a page control with a layout control's name: fine when the requirement scopes it, else the layout's one hides
    layout_pairs = set(_pairs(_dicts(plan["layout"]["controls"])))
    for page in pages:
        if page.get("layout"):
            for role, name in dict.fromkeys(_pairs(_dicts(page.get("controls", [])))):
                if (role, name) in layout_pairs and name:
                    notes.append(f'page {page.get("id")} shows {role} "{name}" like the layout; unless the requirement acts on '
                                 f'it inside a named form or region, hide the layout\'s "{name}" on route {page.get("route")}')

    notes += validate_plan(plan, spec, notes_text)
    return list(dict.fromkeys(notes))


def _parses(text: str) -> bool:
    try:
        _parse(text)
    except ValueError:
        return False
    return True


def make_plan(spec: Spec, notes_text: str, llm, *, temperature: float) -> tuple[dict, list[str]]:
    """One plan call (with its continuation); an answer that is no usable JSON object gets one retry with the error.
    Code then settles the plan (settle_plan); what it could not repair goes back as notes, also kept in
    plan["plan_notes"], where every code and repair call reads them."""
    system = load_prompt("rules")
    base = build_context(spec, notes_text) + "\n\n" + load_prompt("plan")
    user, purpose, error = base, "plan", ""
    for attempt in range(2):
        answer = llm.complete_long(purpose, system, user, max_tokens=config.MAX_TOKENS["plan"], json_mode=True,
                                   temperature=temperature, done=_parses)
        try:
            plan = _parse(answer.text)
        except ValueError as e:
            error = str(e)
        else:
            notes = settle_plan(plan, spec, notes_text)
            if not _shape_problems(plan):
                if notes:
                    plan["plan_notes"] = notes
                return plan, notes
            error = "; ".join(notes)
        purpose, user = "plan-retry", base + "\n\n" + fill(load_prompt("plan_fix"), error=error)
    raise PlanError(f"no plan answer was a usable JSON object after 2 attempts: {error}")
