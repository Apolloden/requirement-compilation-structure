"""Static checks of the generated files against the plan, in under a second, with no model and no build."""
from __future__ import annotations

import re

from arcflash import config
from arcflash.plan import page_component

SHELL = ("backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx")
METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "del": "DELETE", "delete": "DELETE"}
FRONTEND_CALL = re.compile(r"\bapi\.(get|post|put|patch|del)\(\s*(?=[\"'`]/api/)")
BACKEND_ROUTE = re.compile(r"\bapp\.(get|post|put|patch|delete)\(\s*([\"'`])(/api/[^\"'`]*)\2")
EXPRESSION = re.compile(r"[\w$.]+")
PLUS = re.compile(r"\s*\+\s*")
DIALOG_CALL = re.compile(r"\bwindow\.(alert|confirm|prompt)\s*\(|(?<![\w$.])(?<!function )(alert|confirm|prompt)\s*\(")
FORM_TAG = re.compile(r"<form\b(?:=>|[^>])*>")
BAD_INPUT = re.compile(r"\btype\s*=\s*\{?\s*[\"'`](date|number)[\"'`]")
CONTROL_TAGS = {"link": "Link|NavLink|a", "button": "button"}   # JSX tags that render a layout link or button
IMPORT = re.compile(r"""\bimport\s+(?:[\w$*{}\s,]+?\s+from\s+)?["']([^"']+)["']|\bimport\(\s*["']([^"']+)["']\s*\)|\brequire\(\s*["']([^"']+)["']\s*\)""")


def normalize_api_path(path: str) -> str:
    """Drop the query; ${...} and :name segments become :p; drop a trailing slash (except for "/")."""
    path = path.split("?", 1)[0]
    segments = [":p" if ("${" in s or s.startswith(":")) else s for s in path.split("/")]
    path = "/".join(segments)
    return path.rstrip("/") or "/"


def _read_literal(src: str, i: int) -> tuple[str, int] | None:
    """The text of the string literal starting at src[i] and the index after it, or None."""
    quote = src[i] if i < len(src) else ""
    if quote not in "\"'`":
        return None
    j = i + 1
    while j < len(src) and src[j] != quote:
        j += 2 if src[j] == "\\" else 1
    return (src[i + 1:j], j + 1) if j < len(src) else None


def _skip_balanced(src: str, i: int) -> int:
    """After an identifier: skip one balanced (...) or [...] group if it follows."""
    if i >= len(src) or src[i] not in "([":
        return i
    depth = 0
    while i < len(src):
        if src[i] in "([":
            depth += 1
        elif src[i] in ")]":
            depth -= 1
        i += 1
        if depth == 0:
            break
    return i


def _read_path_argument(src: str, i: int) -> str | None:
    """Read "lit" (+ expr + "lit")* starting at src[i]; each expression becomes one ${} placeholder."""
    parts = []
    while True:
        literal = _read_literal(src, i)
        if literal is None:
            return "".join(parts) or None
        text, i = literal
        parts.append(text)
        while True:  # after a literal: "+ expr", "+ literal" or the end of the argument
            m = PLUS.match(src, i)
            if not m:
                return "".join(parts)
            i = m.end()
            if i < len(src) and src[i] in "\"'`":
                break  # another literal
            e = EXPRESSION.match(src, i)
            if not e:
                return "".join(parts)
            i = _skip_balanced(src, e.end())
            parts.append("${}")


def frontend_api_calls(sources: dict[str, str]) -> set[tuple[str, str]]:
    calls = set()
    for src in sources.values():
        for m in FRONTEND_CALL.finditer(src):
            path = _read_path_argument(src, m.end())
            if path:
                calls.add((METHODS[m.group(1)], normalize_api_path(path)))
    return calls


def route_serves(route: str, call: str) -> bool:
    """Express semantics: a :p segment of the route matches any one segment of the call."""
    r, c = route.split("/"), call.split("/")
    return len(r) == len(c) and all(a == b or a == ":p" for a, b in zip(r, c))


def backend_routes(source: str) -> set[tuple[str, str]]:
    return {(METHODS[m.group(1)], normalize_api_path(m.group(3))) for m in BACKEND_ROUTE.finditer(source)}


def _exports(source: str, name: str) -> bool:
    n = re.escape(name)
    return bool(re.search(rf"\bexport\s+(?:async\s+)?function\s+{n}\b|\bexport\s+(?:const|let|var)\s+{n}\b"
                          rf"|\bexport\s*\{{[^}}]*\b{n}\b[^}}]*\}}", source))


def _name_present(name: str, sources: list[str]) -> bool:
    """The name stands whole between quotes or between > and <, surrounding whitespace ignored."""
    n = re.escape(name.strip())
    pattern = re.compile(rf"([\"'`])\s*{n}\s*\1|>\s*{n}\s*<")
    return any(pattern.search(src) for src in sources)


# Rule 6: the roles named elements render, against the planned roles (evidence.md C1).
CONTAINER_ROLES = ("navigation", "region", "article", "row", "listitem", "group", "status", "timer", "dialog", "tabpanel")
TAG_ROLE = {"nav": "navigation", "section": "region", "article": "article", "tr": "row", "li": "listitem", "fieldset": "group",
            "dialog": "dialog", "aside": "complementary", "header": "banner", "footer": "contentinfo", "main": "main",
            "form": "form", "ul": "list", "ol": "list", "a": "link", "Link": "link", "NavLink": "link", "button": "button"}
ADVICE = {"navigation": '<nav aria-label="{name}">', "region": '<section aria-label="{name}">',
          "article": '<article aria-label="{name}">', "row": '<tr aria-label="{name}">', "listitem": '<li aria-label="{name}">',
          "group": '<div role="group" aria-label="{name}">', "status": '<div role="status" aria-label="{name}">',
          "timer": '<div role="timer" aria-label="{name}">', "dialog": '<div role="dialog" aria-label="{name}">',
          "tabpanel": '<div role="tabpanel" aria-label="{name}">'}
ROLE_HELPERS = ("region", "navigation", "row", "listitem", "article", "group", "status", "searchbox", "spinbutton",
                "tab", "tabpanel", "menuitem")
ARIA_LABEL = re.compile(r"(?<![\w-])aria-label\s*=\s*")
TAG_START = re.compile(r"<([A-Za-z][\w.]*)")
COMPONENT_LABEL = re.compile(r"(?<![\w-])(?:name|label|title|ariaLabel)\s*=\s*")
EXPLICIT_ROLE = re.compile(r"(?<![\w-])role\s*=\s*(\{?)\s*([\"'`]?)([\w-]*)")
HELPER_CALL = re.compile(rf"(?<![\w$.])({'|'.join(ROLE_HELPERS)})\(\s*[\w$.]+\s*,\s*")
PLACEHOLDER = re.compile(r"(?<![\w-])placeholder\s*=\s*")
ESCAPE = re.compile(r"\\(.)", re.S)


def _unescape(text: str) -> str:
    return ESCAPE.sub(lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text)


def _attribute_value(src: str, i: int) -> str | None:
    """The literal value of a JSX attribute starting at src[i]: "x", 'x' (raw, as JSX reads them) or {"x"} (a JS string)."""
    if i < len(src) and src[i] in "\"'":
        end = src.find(src[i], i + 1)
        return src[i + 1:end] if end > 0 else None
    if i < len(src) and src[i] == "{":
        j = i + 1
        while j < len(src) and src[j].isspace():
            j += 1
        literal = _read_literal(src, j)
        if literal is None or "${" in literal[0]:
            return None
        k = literal[1]
        while k < len(src) and src[k].isspace():
            k += 1
        return _unescape(literal[0]) if k < len(src) and src[k] == "}" else None
    return None


def _tag_end(src: str, i: int) -> int:
    """Index after the ">" that ends the opening tag whose attributes start at src[i]; braces and quotes are skipped."""
    depth, quote = 0, ""
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in "\"'`":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth <= 0:
            return i + 1
        i += 1
    return len(src)


def _rendered_roles(src: str, name: str) -> list[tuple[str, str | None]]:
    """(tag, role) for every element whose aria-label is exactly the name; role None when unknown (a component, role={x})."""
    hits = []
    for m in ARIA_LABEL.finditer(src):
        value = _attribute_value(src, m.end())
        if value is None or value.strip() != name.strip():
            continue
        starts = list(TAG_START.finditer(src, 0, m.start()))
        if not starts:
            continue
        start = starts[-1]
        tag = start.group(1)
        explicit = EXPLICIT_ROLE.search(src, start.end(), _tag_end(src, start.end()))
        if explicit:
            role = explicit.group(3) if explicit.group(2) else None
        elif tag in TAG_ROLE:
            role = TAG_ROLE[tag]
        else:
            role = None if tag[0].isupper() else "generic"
        hits.append((tag, role))
    return hits


AREA_TAGS = frozenset({"section", "nav", "div", "form", "article", "tr", "li", "dialog", "aside", "header", "footer",
                       "fieldset", "table", "tbody", "ul", "ol", "main"})


def _element_spans(src: str, name: str) -> list[tuple[int, int, int]]:
    """(start, end of the opening tag, end) of every plain element whose literal aria-label is the name, from its opening tag to the matching
    closing tag (nested tags of the same name counted). Components and computed labels are left out."""
    spans = []
    labels = [(m, False) for m in ARIA_LABEL.finditer(src)] + [(m, True) for m in COMPONENT_LABEL.finditer(src)]
    for m, component in sorted(labels, key=lambda x: x[0].start()):
        value = _attribute_value(src, m.end())
        starts = list(TAG_START.finditer(src, 0, m.start()))
        if value is None or value.strip() != name.strip() or not starts:
            continue
        # a plain area tag with aria-label, or a component that takes the area's name (<Region name="Question feed">)
        if not (starts[-1].group(1)[:1].isupper() if component else starts[-1].group(1) in AREA_TAGS):
            continue
        line_start = src.rfind("\n", 0, m.start()) + 1
        if src[line_start:m.start()].lstrip().startswith(("//", "*", "/*")):
            continue   # a usage example in a comment
        start, tag = starts[-1].start(), starts[-1].group(1)
        i = _tag_end(src, starts[-1].end())
        opened = i
        if src[i - 2] == "/":
            spans.append((start, i, i))
            continue
        depth, pattern, end = 1, re.compile(rf"<(/?){tag}\b"), len(src)
        while depth:
            t = pattern.search(src, i)
            if not t:
                break
            if t.group(1):
                depth, i = depth - 1, t.end()
                end = i
            else:
                i = _tag_end(src, t.end())
                depth += src[i - 2] != "/"
        spans.append((start, opened, end if not depth else len(src)))
    return spans


ANY_TAG = re.compile(r"<(/?)([A-Za-z][\w.]*)")
INLINE_TAGS = frozenset({"span", "strong", "em", "b", "i", "small", "u", "mark", "abbr"})
CONTROL_ELEMENTS = {"link": frozenset({"a", "Link", "NavLink"}), "button": frozenset({"button"})}
ENCLOSING_WINDOW = 4000   # how far back the enclosing tags are read


def _enclosing(src: str, pos: int) -> list[tuple[str, int, int]]:
    """Unclosed opening tags before pos, nearest last: (tag, start, end of the opening tag)."""
    stack: list[tuple[str, int, int]] = []
    for m in ANY_TAG.finditer(src, max(0, pos - ENCLOSING_WINDOW), pos + 1):
        if m.group(1):
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == m.group(2):
                    del stack[k:]
                    break
            continue
        end = _tag_end(src, m.end())
        if src[end - 2] != "/":
            stack.append((m.group(2), m.start(), end))
    return stack


def _rendered_as(src: str, pos: int, role: str) -> str | None:
    """None when the text at pos sits in an element of the role (or in a component, which may render one); else the
    tag it sits in. Inline wrappers such as <span> are looked through."""
    for tag, start, end in reversed(_enclosing(src, pos)):
        explicit = EXPLICIT_ROLE.search(src, start, end)
        if tag in CONTROL_ELEMENTS[role] or tag[0].isupper() or (explicit and explicit.group(3) == role):
            return None
        if tag not in INLINE_TAGS:
            return tag
    return None


def text_control_issues(files: dict[str, str], plan: dict) -> list[str]:
    """Rule 2: a planned link or button whose name appears in the frontend only as plain text (evidence.md 6.3)."""
    issues = []
    frontend = {k: v for k, v in files.items() if k.startswith("frontend/")}
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    controls = [c for c in layout.get("controls", []) if isinstance(c, dict)]
    controls += [c for p in plan.get("pages", []) if isinstance(p, dict) for c in p.get("controls", []) if isinstance(c, dict)]
    seen = set()
    for c in controls:
        role, name = c.get("role"), str(c.get("name", "")).strip()
        if role not in CONTROL_ELEMENTS or not name or "{" in name or (role, name) in seen:
            continue
        seen.add((role, name))
        text = re.compile(rf">\s*{re.escape(name)}\s*<")
        found = [(key, m.start(), _rendered_as(src, m.start(), role)) for key, src in frontend.items() for m in text.finditer(src)]
        literal = re.compile(rf"([\"'`]){re.escape(name)}\1")
        elsewhere = any(literal.search(src) or any(r in (role, None) for _, r in _rendered_roles(src, name))
                        for src in frontend.values())   # data ({label: "X"}) or aria-label="X": not judged
        if found and not elsewhere and all(tag is not None for _, _, tag in found):
            key, _, tag = found[0]
            fix = "<Link to>" if role == "link" else "<button>"
            issues.append(f'{role} "{name}" is rendered as text in <{tag}> in {key}; use {fix} (rule 2)')
    return issues


COMPONENT_NAME = re.compile(r"^(?:export\s+(?:default\s+)?)?(?:function\s+([A-Z][\w$]*)|const\s+([A-Z][\w$]*)\s*=)", re.M)


def _component_at(src: str, pos: int) -> str | None:
    """The name of the top-level component whose definition starts last before pos."""
    found = [m.group(1) or m.group(2) for m in COMPONENT_NAME.finditer(src, 0, pos)]
    return found[-1] if found else None


SEED_SHOWN = 30


def seed_issues(files: dict[str, str], plan: dict) -> list[str]:
    """One issue naming every seed value (email, username, number ... from the Seed prerequisites) that backend/app.js
    does not contain literally. On the six graded ArcLaser builds it flags exactly Stack Overflow's 18 missing accounts."""
    backend = files.get("backend/app.js")
    values = [v for v in plan.get("seed_values", []) if isinstance(v, str)] if isinstance(plan.get("seed_values"), list) else []
    if backend is None or not values:
        return []
    missing = [v for v in values if v not in backend]
    if not missing:
        return []
    shown = ", ".join(f'"{v}"' for v in missing[:SEED_SHOWN]) + (f" and {len(missing) - SEED_SHOWN} more" if len(missing) > SEED_SHOWN else "")
    return [f"backend/app.js seeds no record with {shown}; the scenarios' Seed prerequisites list them: seed every listed "
            "record with its exact values (rule 12). Every scenario that signs in with a missing account fails."]


def area_issues(files: dict[str, str], plan: dict) -> list[str]:
    """Rule 6b: a control planned "in" a named area must be rendered inside that area's element (evidence.md 6.2).
    Judged only in a file that renders both the area (a plain tag with a literal aria-label) and the control's name."""
    issues = []
    frontend = {k: v for k, v in files.items() if k.startswith("frontend/")}
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    groups = [[c for c in layout.get("controls", []) if isinstance(c, dict)]]
    groups += [[c for c in p.get("controls", []) if isinstance(c, dict)] for p in plan.get("pages", []) if isinstance(p, dict)]
    for controls in groups:
        for c in controls:
            area, name = c.get("in"), str(c.get("name", "")).strip()
            if not isinstance(area, str) or not area.strip() or not name:
                continue
            area_role = next((str(x.get("role")) for x in controls + groups[0] if x is not c and x.get("name") == area), "region")
            n = re.escape(name)
            occurrence = re.compile(rf"([\"'`])\s*{n}\s*\1|>\s*{n}\s*<")
            for key, src in frontend.items():
                spans = _element_spans(src, area)
                places = [m.start() for m in occurrence.finditer(src)]
                places = [p for p in places if not any(a <= p < o for a, o, _ in spans)]   # not the area's own label
                # an occurrence inside a component that the area renders (<Row /> in the span) counts as inside
                used = {m.group(1) for a, _, b in spans for m in re.finditer(r"<([A-Z][\w]*)", src[a:b])}
                inside = any(o <= p < b for p in places for _, o, b in spans) or any(_component_at(src, p) in used for p in places)
                # a component area that gets the control as data in its props (<RadioGroup name="Gender" options={["Male"]} />)
                inside = inside or any(src[a + 1:a + 2].isupper() and re.search(rf"([\"'`])\s*{n}\s*\1", src[a:o])
                                       for a, o, _ in spans)
                # a list the area builds from data (.map) with the name as a data value: not judged, like Task 11
                inside = inside or (any(".map(" in src[o:b] for _, o, b in spans)
                                    and re.search(rf"(?<!=)(?<!=\{{)([\"'`]){n}\1", src) is not None)   # not an attribute
                if spans and places and not inside:
                    issues.append(f'{c.get("role")} "{name}" is outside {area_role} "{area}" in {key}; '
                                  'render it inside that element (the requirement puts it there)')
    return list(dict.fromkeys(issues))


def _placeholders(sources: list[str]) -> set[str]:
    return {v.strip() for src in sources for m in PLACEHOLDER.finditer(src) if (v := _attribute_value(src, m.end())) is not None}


def role_issues(files: dict[str, str], controls: list[dict]) -> list[str]:
    """Named areas whose rendered role differs from the planned one, and our tests looking up a planned name with another role."""
    issues = []
    frontend = {k: v for k, v in files.items() if k.startswith("frontend/")}
    planned: dict[str, set[str]] = {}
    for control in controls:
        name = str(control.get("name", "")).strip()
        if name:
            planned.setdefault(name, set()).add(str(control.get("role", "")))
    for control in controls:
        role, name = str(control.get("role", "")), str(control.get("name", "")).strip()
        if role not in CONTAINER_ROLES or not name:
            continue
        hits = [(key, tag, r) for key, src in frontend.items() for tag, r in _rendered_roles(src, name)]
        if hits and all(r is not None for _, _, r in hits) and not any(r == role for _, _, r in hits):
            key, tag, rendered = hits[0]
            issues.append(f'{role} "{name}" is rendered as <{tag}> ({rendered}) in {key}; use {ADVICE[role].format(name=name)} (rule 6)')
    for key, src in files.items():
        if not (key.startswith("tests/") and key.endswith(".spec.ts")):
            continue
        for m in HELPER_CALL.finditer(src):
            literal = _read_literal(src, m.end())
            if literal is None or "${" in literal[0]:
                continue
            name, helper = _unescape(literal[0]).strip(), m.group(1)
            if name in planned and helper not in planned[name]:
                issues.append(f'{key} looks up "{name}" as {helper}; the plan and the requirements say {" or ".join(sorted(planned[name]))}')
    return list(dict.fromkeys(issues))


# Rule 16: a menu opened on pointer enter must not be toggled by a click (evidence.md C2).
HOVER_ATTR = re.compile(r"(?<![\w$])on(?:MouseEnter|MouseOver|PointerEnter)\s*=\s*\{")
SET_TRUE = re.compile(r"(?<![\w$.])([\w$]+)\(\s*true\s*\)")
COMPONENT_START = re.compile(r"^(?:export\s+(?:default\s+)?)?(?:function\s+[A-Z]|const\s+[A-Z][\w$]*\s*=)", re.M)
HANDLER_CHARS = 400   # how much of a named handler's definition is read for setX(true)


def _brace_end(src: str, i: int) -> int:
    """src[i] is "{": the index after its matching "}"."""
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return j + 1
    return len(src)


def _hover_setters(component: str, src: str) -> list[str]:
    """Setters called with true by the pointer-enter handlers of one component (inline or a named handler in the file)."""
    setters = []
    for m in HOVER_ATTR.finditer(component):
        body = component[m.end():_brace_end(component, m.end() - 1) - 1]
        if re.fullmatch(r"\s*[\w$]+\s*", body):
            d = re.search(rf"\b(?:const|let|var|function)\s+{re.escape(body.strip())}\b", src)
            body = src[d.end():d.end() + HANDLER_CHARS] if d else ""
        setters += SET_TRUE.findall(body)
    return list(dict.fromkeys(setters))


def _toggles(src: str, setter: str) -> bool:
    """setter(v => !v), setter((v) => !v) or setter(!state)."""
    s = re.escape(setter)
    return bool(re.search(rf"(?<![\w$.]){s}\(\s*(?:\(?\s*([\w$]+)\s*\)?\s*=>\s*!\s*\1\b|![\w$.]+\s*\))", src))


def menu_issues(frontend: dict[str, str]) -> list[str]:
    issues = []
    for key, src in frontend.items():
        starts = [0] + [m.start() for m in COMPONENT_START.finditer(src)] + [len(src)]
        components = [src[a:b] for a, b in zip(starts, starts[1:])]
        setters = [s for c in components for s in _hover_setters(c, src) if _toggles(c, s)]
        for setter in dict.fromkeys(setters):
            issues.append(f"{key} opens a menu on hover and toggles it on click; a click right after the hover closes it. "
                          f"Make the click open it ({setter}(true)) (rule 16)")
    return issues


def _import_allowed(spec: str, packages: frozenset) -> bool:
    return spec.startswith(("./", "../", "node:")) or spec in config.NODE_BUILTINS or spec in packages


def static_issues(files: dict[str, str], plan: dict) -> list[str]:
    issues: list[str] = []
    pages = [p for p in plan.get("pages", []) if isinstance(p, dict)]
    modules = [m["id"] for m in plan.get("modules", []) if isinstance(m, dict) and m.get("id")]
    module_keys = [f"frontend/src/pages/{m}.jsx" for m in modules]
    frontend = {k: v for k, v in files.items() if k.startswith("frontend/")}
    backend = files.get("backend/app.js")
    routes_src = files.get("frontend/src/App.jsx")

    # 1-2: files and the backend export
    issues += [f"missing file {key}" for key in [*SHELL, *module_keys] if key not in files]
    if backend is not None and not _exports(backend, "registerApi"):
        issues.append("backend/app.js does not export registerApi")

    # 3-4: page components and routes
    for page in pages:
        key = f"frontend/src/pages/{page.get('module')}.jsx"
        if key in files and page.get("id") and not _exports(files[key], page_component(page["id"])):
            issues.append(f"{key} does not export {page_component(page['id'])}")
    if routes_src is not None:
        for page in pages:
            route = page.get("route")
            if route and f'"{route}"' not in routes_src and f"'{route}'" not in routes_src:
                issues.append(f'frontend/src/App.jsx has no route "{route}"')

    # 5-7: frontend calls, planned API and backend routes
    if backend is not None:
        routes = backend_routes(backend)
        if not routes:
            issues.append('backend/app.js registers no app.<method>("/api/...") routes; register every route on app with its full path')
        else:
            for method, path in sorted(frontend_api_calls(frontend) - routes):
                if any(m == method and route_serves(r, path) for m, r in routes):
                    continue
                issues.append(f"frontend calls {method} {path} but backend/app.js has no such route")
            for api in plan.get("api", []):
                if isinstance(api, dict) and api.get("path"):
                    method = str(api.get("method", "")).upper()
                    if (method, normalize_api_path(api["path"])) not in routes:
                        issues.append(f"plan API {method} {api['path']} is not implemented in backend/app.js")

    # 8: every planned name appears in the frontend code, or in the backend that seeds it (a value from the database)
    sources = list(frontend.values())
    name_sources = sources + ([backend] if backend is not None else [])
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    named = [(c, "layout") for c in layout.get("controls", []) if isinstance(c, dict)]
    named += [(c, f"page {p.get('id')}") for p in pages for c in p.get("controls", []) if isinstance(c, dict)]
    for control, where in named:
        name = str(control.get("name", ""))
        if name.strip() and not _name_present(name, name_sources):
            issues.append(f'{control.get("role")} "{name}" ({where}) does not appear in the frontend code')

    # 8-placeholder: a field named with hint text is also found by its placeholder (rule 3, evidence.md C3)
    placeholders = _placeholders(sources)
    for control, where in named:
        name = str(control.get("name", "")).strip()
        if control.get("role") == "textbox" and name.endswith(("...", "…")) and name not in placeholders:
            issues.append(f'textbox "{name}" ({where}) needs placeholder="{name}" as well as its label (rule 3)')

    # 8a: the roles named areas render, in the code and in our tests (rule 6)
    issues += role_issues(files, [c for c, _ in named])

    # 8b: a layout link or button repeated inside a page file (the layout already shows it on every page)
    layout_modules = sorted({f"frontend/src/pages/{p.get('module')}.jsx" for p in pages if p.get("layout")})
    for control in layout.get("controls", []):
        if not isinstance(control, dict) or control.get("role") not in CONTROL_TAGS or not str(control.get("name", "")).strip():
            continue
        name = str(control["name"]).strip()
        tag = re.compile(rf"<({CONTROL_TAGS[control['role']]})\b(?:=>|[^>])*>\s*{re.escape(name)}\s*</\1>")
        for key in layout_modules:
            if key in files and tag.search(files[key]):
                issues.append(f'{key} repeats the layout {control["role"]} "{name}"; a screen shows it once unless the requirement '
                              'acts on it inside a named form or region: remove a repeat (a breadcrumb), or keep a form\'s own '
                              'control and hide the layout\'s one on that page (rule 5)')

    # 8a2: every record the scenarios' Seed prerequisites name is seeded (ArcLaser SO: 18 of 24 accounts missing)
    issues += seed_issues(files, plan)

    # 8b2: controls planned inside a named area are rendered inside it (Task 6b)
    issues += area_issues(files, plan)

    # 8b3: a planned link or button rendered only as plain text (Task 11)
    issues += text_control_issues(files, plan)

    # 8c: hover menus that a click closes (rule 16)
    issues += menu_issues(frontend)

    # 9-11: browser dialogs, input types and imports; type="number" is the spinbutton the requirements may state
    allowed_types = {"number"} if any(c.get("role") == "spinbutton" for c, _ in named) else set()
    for key, src in frontend.items():
        for kind in sorted({m.group(1) or m.group(2) for m in DIALOG_CALL.finditer(src)}):
            issues.append(f"{key} uses window.{kind}; use an in-page role=dialog or role=alert instead")
        for kind in sorted({m.group(1) for m in BAD_INPUT.finditer(src)} - allowed_types):
            issues.append(f'{key} uses an input of type "{kind}"; use type="text" (rule 3)')
        if any("noValidate" not in m.group(0) for m in FORM_TAG.finditer(src)):
            issues.append(f'{key} has a <form> without noValidate; add noValidate and show problems with role="alert" (rule 3)')
    sides = [("backend/app.js", backend, config.BACKEND_PACKAGES)] + [(k, v, config.FRONTEND_PACKAGES) for k, v in frontend.items()]
    for key, src, packages in sides:
        if src is None:
            continue
        for spec in dict.fromkeys(next(g for g in m.groups() if g) for m in IMPORT.finditer(src)):
            if not _import_allowed(spec, packages):
                issues.append(f'{key} imports "{spec}", which is not installed; use only the fixed stack')

    return list(dict.fromkeys(issues))
