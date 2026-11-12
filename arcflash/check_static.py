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
                issues.append(f"frontend calls {method} {path} but backend/app.js has no such route")
            for api in plan.get("api", []):
                if isinstance(api, dict) and api.get("path"):
                    method = str(api.get("method", "")).upper()
                    if (method, normalize_api_path(api["path"])) not in routes:
                        issues.append(f"plan API {method} {api['path']} is not implemented in backend/app.js")

    # 8: every planned name appears in the frontend code
    sources = list(frontend.values())
    layout = plan.get("layout") if isinstance(plan.get("layout"), dict) else {}
    named = [(c, "layout") for c in layout.get("controls", []) if isinstance(c, dict)]
    named += [(c, f"page {p.get('id')}") for p in pages for c in p.get("controls", []) if isinstance(c, dict)]
    for control, where in named:
        name = str(control.get("name", ""))
        if name.strip() and not _name_present(name, sources):
            issues.append(f'{control.get("role")} "{name}" ({where}) does not appear in the frontend code')

    # 8b: a layout link or button repeated inside a page file (the layout already shows it on every page)
    layout_modules = sorted({f"frontend/src/pages/{p.get('module')}.jsx" for p in pages if p.get("layout")})
    for control in layout.get("controls", []):
        if not isinstance(control, dict) or control.get("role") not in CONTROL_TAGS or not str(control.get("name", "")).strip():
            continue
        name = str(control["name"]).strip()
        tag = re.compile(rf"<({CONTROL_TAGS[control['role']]})\b(?:=>|[^>])*>\s*{re.escape(name)}\s*</\1>")
        for key in layout_modules:
            if key in files and tag.search(files[key]):
                issues.append(f'{key} repeats the layout {control["role"]} "{name}"; the layout already shows it on every page (rule 5)')

    # 9-11: browser dialogs, input types and imports
    for key, src in frontend.items():
        for kind in sorted({m.group(1) or m.group(2) for m in DIALOG_CALL.finditer(src)}):
            issues.append(f"{key} uses window.{kind}; use an in-page role=dialog or role=alert instead")
        for kind in sorted({m.group(1) for m in BAD_INPUT.finditer(src)}):
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
