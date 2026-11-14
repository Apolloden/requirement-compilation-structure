"""The plan: one JSON object that fixes tables, API, layout, pages and modules before any code is written."""
from __future__ import annotations

import json
import re

from arcflash import config
from arcflash.prompting import build_context, fill, load_prompt
from arcflash.spec import Spec

SNAKE = re.compile(r"[a-z][a-z0-9_]*")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
ATTEMPTS = 3
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
    problems += [f"plan.{key} must be a list" for key in ("tables", "api", "pages", "modules") if not isinstance(plan.get(key), list)]
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
            problems.append(f'{where}: control name "{name}" is not in the requirements or screenshot notes; copy names exactly')
        if role in STATED_ONLY_ROLES and name.strip() and not _role_stated(spec, role, name):
            problems.append(f'{where}: {role} "{name}" has a role the requirements do not state; '
                            'an action they name without a role is a link or a button')
    return problems


def _pairs(controls: list[dict]) -> list[tuple[str, str]]:
    return [(c.get("role"), c.get("name")) for c in controls]


def validate_plan(plan: dict, spec: Spec, notes_text: str = "") -> list[str]:
    """Every rule of Task 8, with its exact problem text. An empty list means the plan is usable as it is."""
    problems = _shape_problems(plan)
    if problems:
        return problems
    pages, apis, modules, tables = _dicts(plan["pages"]), _dicts(plan["api"]), _dicts(plan["modules"]), _dicts(plan["tables"])
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
    layout_pairs = _pairs(layout_controls)
    for pair in dict.fromkeys(p for p in layout_pairs if layout_pairs.count(p) > 1):
        problems.append(f'layout: {pair[0]} "{pair[1]}" appears twice')
    for n, page in enumerate(pages, start=1):
        controls = _dicts(page.get("controls", []))
        problems += _control_problems(names_of[n], controls, spec, notes_text)
        pairs = _pairs(controls)
        for pair in dict.fromkeys(p for p in pairs if pairs.count(p) > 1):
            problems.append(f'{names_of[n]}: {pair[0]} "{pair[1]}" appears twice')
        if page.get("layout"):
            for pair in dict.fromkeys(p for p in pairs if p in layout_pairs):
                problems.append(f'{names_of[n]}: {pair[0]} "{pair[1]}" repeats a layout control; header controls belong only in layout')

    # 8: every "Name"(role=x) of the text is planned
    planned = set(layout_pairs) | {pair for page in pages for pair in _pairs(_dicts(page.get("controls", [])))}
    problems += [f'the requirements name {role} "{name}", but no page or layout control has it'
                 for role, name in spec.role_names if (role, name) not in planned]

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


def settle_modules(plan: dict) -> None:
    """Make the module lists and each page's "module" agree, in place, without a model call (v4 plan R6):
    the list wins; a page that no list has joins the module it names; a module over MAX_PAGES_PER_MODULE is split
    in list order into new modules ("main", "main_2", ...). Code generation reads the lists, the checks and the repair
    file choice read page["module"]; after this both say the same."""
    if not isinstance(plan.get("pages"), list) or not isinstance(plan.get("modules"), list):
        return
    modules = {m["id"]: m for m in _dicts(plan["modules"]) if isinstance(m.get("id"), str) and isinstance(m.get("pages"), list)}
    for page in _dicts(plan["pages"]):
        pid = page.get("id")
        owners = [mid for mid, m in modules.items() if pid in m["pages"]]
        if not owners and page.get("module") in modules:
            modules[page["module"]]["pages"].append(pid)
    taken = {m.get("id") for m in _dicts(plan["modules"])}
    settled = []
    for module in plan["modules"]:
        settled.append(module)
        if not (isinstance(module, dict) and module.get("id") in modules):
            continue
        listed, size, n = module["pages"], config.MAX_PAGES_PER_MODULE, 1
        module["pages"] = listed[:size]
        for start in range(size, len(listed), size):
            while f"{module['id']}_{n}" in taken or n == 1:
                n += 1
            taken.add(f"{module['id']}_{n}")
            settled.append({"id": f"{module['id']}_{n}", "pages": listed[start:start + size]})
    plan["modules"] = settled
    owner = {}
    for module in _dicts(settled):
        for pid in module.get("pages", []) if isinstance(module.get("pages"), list) else []:
            owner.setdefault(pid, []).append(module.get("id"))
    for page in _dicts(plan["pages"]):
        if len(owner.get(page.get("id"), [])) == 1:
            page["module"] = owner[page["id"]][0]


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


def _parses(text: str) -> bool:
    try:
        _parse(text)
    except ValueError:
        return False
    return True


def make_plan(spec: Spec, notes_text: str, llm, *, temperature: float) -> tuple[dict, list[str]]:
    """Up to 3 attempts; the first plan without problems wins, else the last plan that parsed, with its problems."""
    system = load_prompt("rules")
    base = build_context(spec, notes_text) + "\n\n" + load_prompt("plan")
    best: dict | None = None
    best_problems: list[str] = []
    problems: list[str] = []
    previous = ""
    for attempt in range(ATTEMPTS):
        if attempt == 0:
            purpose, user = "plan", base
        else:
            purpose = f"plan-fix-{attempt}"
            user = base + "\n\n" + fill(load_prompt("plan_fix"), problems="\n".join(f"- {p}" for p in problems),
                                        previous=previous)
        answer = llm.complete_long(purpose, system, user, max_tokens=config.MAX_TOKENS["plan"], json_mode=True,
                                   temperature=temperature, done=_parses)
        previous = answer.text
        try:
            plan = _parse(answer.text)
        except ValueError as e:
            problems = [f"the answer is not one valid JSON object: {e}"]
            continue
        settle_modules(plan)
        settle_areas(plan)
        problems = validate_plan(plan, spec, notes_text)
        best, best_problems = plan, problems
        if not problems:
            return plan, []
    if best is None:
        raise PlanError(f"no plan answer was a JSON object after {ATTEMPTS} attempts: {problems[0]}")
    return best, best_problems
