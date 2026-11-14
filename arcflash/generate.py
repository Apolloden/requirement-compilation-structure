"""Code generation from the plan (backend and frontend in parallel) and the independent scenario-test writer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from arcflash import config
from arcflash.blocks import parse_file_blocks, render_files
from arcflash.llm import LLMError
from arcflash.plan import page_component
from arcflash.prompting import build_context, fill, load_prompt
from arcflash.spec import Spec

BACKEND = "backend/app.js"
SHARED = "frontend/src/shared.jsx"
APP = "frontend/src/App.jsx"


class GenerationError(RuntimeError):
    pass


def _modules(plan: dict) -> list[dict]:
    return [m for m in plan.get("modules", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]


def _module_pages(plan: dict, module_id: str) -> list[str]:
    module = next((m for m in _modules(plan) if m["id"] == module_id), {})
    return [p for p in module.get("pages", []) if isinstance(p, str)]


def module_key(module_id: str) -> str:
    return f"frontend/src/pages/{module_id}.jsx"


def module_imports(plan: dict) -> str:
    """One import line per module, components in page order."""
    return "\n".join(f'import {{ {", ".join(page_component(p) for p in _module_pages(plan, m["id"]))} }} from "./pages/{m["id"]}.jsx";'
                     for m in _modules(plan))


def components_text(plan: dict, module_id: str) -> str:
    routes = {p.get("id"): p.get("route") for p in plan.get("pages", []) if isinstance(p, dict)}
    return ", ".join(f"{page_component(p)} (page {p}, route {routes.get(p)})" for p in _module_pages(plan, module_id))


def stub_module(module_id: str, components: list[str]) -> str:
    body = "".join(f"\nexport function {name}() {{\n  return <h1>Not built</h1>;\n}}\n" for name in components)
    return f"// Module \"{module_id}\" was not generated; these stubs keep the build working.\n{body}"


def _ask(llm, purpose: str, system: str, user: str, expected: list[str], kind: str, temperature: float,
         background: bool = False) -> dict[str, str]:
    """One call; the expected file blocks that are missing are asked for once more, showing the files already kept so
    the new ones fit them. When the second answer has every expected file, it is used whole (v4 plan R4)."""
    def whole(text: str) -> bool:
        return all(k in parse_file_blocks(text) for k in expected)

    answer = llm.complete_long(purpose, system, user, max_tokens=config.MAX_TOKENS[kind], temperature=temperature,
                               background=background, done=whole)
    files = {k: v for k, v in parse_file_blocks(answer.text).items() if k in expected}
    missing = [k for k in expected if k not in files]
    if missing:
        task = fill(load_prompt("missing_files"), paths=", ".join(missing))
        if files:
            task += "\n\n" + fill(load_prompt("kept_files"), files=render_files(files))
        again = llm.complete_long(f"{purpose}-again", system, user + "\n\n" + task, max_tokens=config.MAX_TOKENS[kind],
                                  temperature=temperature, background=background, done=whole)
        more = {k: v for k, v in parse_file_blocks(again.text).items() if k in expected}
        if len(more) == len(expected):
            return more
        files.update({k: more[k] for k in missing if k in more})
    return files


def generate_app(spec: Spec, notes_text: str, plan: dict, llm, *, temperature: float) -> tuple[dict[str, str], list[str]]:
    """backend/app.js and the first module (with the shell) in parallel; further modules once the shell exists."""
    modules = _modules(plan)
    if not modules:
        raise GenerationError("the plan has no modules")
    context = build_context(spec, notes_text, plan)
    system = load_prompt("rules")
    first = modules[0]["id"]

    def ask(purpose, task, expected, kind):
        return _ask(llm, purpose, system, context + "\n\n" + task, expected, kind, temperature)

    problems: list[str] = []
    with ThreadPoolExecutor(max_workers=config.MAX_PARALLEL_CALLS) as pool:
        backend = pool.submit(ask, "backend", load_prompt("backend"), [BACKEND], "backend")
        shell_task = fill(load_prompt("frontend_first"), module=first, imports=module_imports(plan),
                          components=components_text(plan, first))
        shell = pool.submit(ask, f"frontend:{first}", shell_task, [SHARED, APP, module_key(first)], "frontend")
        try:
            files = shell.result()
        except LLMError as e:
            raise GenerationError(f"the frontend shell call failed: {e}") from e
        if SHARED not in files or APP not in files:
            raise GenerationError("the frontend shell (shared.jsx and App.jsx) was not generated")
        more = {}
        for module in modules[1:]:
            task = fill(load_prompt("frontend_more"), module=module["id"], components=components_text(plan, module["id"]),
                        shared=files[SHARED], app=files[APP])
            more[module["id"]] = pool.submit(ask, f"frontend:{module['id']}", task, [module_key(module["id"])], "frontend")
        try:
            backend_files = backend.result()
        except LLMError as e:
            raise GenerationError(f"the backend call failed: {e}") from e
        if BACKEND not in backend_files:
            raise GenerationError("backend/app.js was not generated")
        files.update(backend_files)
        pages = {first: files.pop(module_key(first), None)}
        for module_id, future in more.items():
            try:
                pages[module_id] = future.result().get(module_key(module_id))
            except LLMError:
                pages[module_id] = None
    for module in modules:
        mid = module["id"]
        if pages.get(mid) is not None:
            files[module_key(mid)] = pages[mid]
        else:
            files[module_key(mid)] = stub_module(mid, [page_component(p) for p in _module_pages(plan, mid)])
            problems.append(f"module {mid} was not generated; a stub was written")
    return files, problems


def write_tests(spec: Spec, llm) -> tuple[dict[str, str], list[str]]:
    """Playwright tests from the digest only (no plan, no screenshot notes), 20 scenarios per call, in parallel."""
    ids = [s.id for s in spec.scenarios]
    size = config.SCENARIOS_PER_TEST_CALL
    chunks = [ids[i:i + size] for i in range(0, len(ids), size)]
    if not chunks:
        return {}, []
    context = build_context(spec)
    system = load_prompt("rules")

    def one(n: int, chunk: list[str]) -> dict[str, str]:
        task = fill(load_prompt("tests"), scenario_ids=", ".join(chunk), n=str(n))
        try:
            return _ask(llm, f"tests:{n}", system, context + "\n\n" + task, [f"tests/scenarios-{n}.spec.ts"], "tests",
                        config.TEMPERATURE, background=True)
        except LLMError:
            return {}

    with ThreadPoolExecutor(max_workers=config.MAX_PARALLEL_CALLS) as pool:
        answers = list(pool.map(one, range(1, len(chunks) + 1), chunks))
    files, problems = {}, []
    for chunk, answer in zip(chunks, answers):
        if answer:
            files.update(answer)
        else:
            problems.append(f"tests for scenarios {chunk[0]} to {chunk[-1]} were not written")
    return files, problems
