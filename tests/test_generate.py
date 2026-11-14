import copy
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from arcflash import config
from arcflash.generate import GenerationError, components_text, generate_app, module_imports, stub_module, write_tests
from arcflash.llm import LLMError
from arcflash.prompting import build_context, load_prompt
from arcflash.spec import load_spec
from tests.fakes import FakeLLM, as_blocks
from tests.golden import golden_files, golden_plan
from tests.support import FIXTURES

NOTES = FIXTURES / "specs" / "notes"
SHELL = ("frontend/src/shared.jsx", "frontend/src/App.jsx")


def pick(*keys, **extra):
    files = golden_files()
    out = {k: files[k] for k in keys}
    out.update(extra)
    return as_blocks(out)


def answers(**overrides):
    script = {"backend": [pick("backend/app.js")],
              "frontend:main": [pick(*SHELL, "frontend/src/pages/main.jsx")]}
    script.update(overrides)
    return FakeLLM(script)


def ten_page_plan():
    plan = copy.deepcopy(golden_plan())
    for i in range(4):
        plan["pages"].append({"id": f"extra_{i}", "route": f"/extra/{i}", "title": "Notes", "layout": True, "module": "main",
                              "reqs": [], "controls": [], "shows": [], "flows": []})
        plan["modules"][0]["pages"].append(f"extra_{i}")
    for pid in ("more_a", "more_b"):
        plan["pages"].append({"id": pid, "route": f"/{pid}", "title": "Notes", "layout": True, "module": "more",
                              "reqs": [], "controls": [], "shows": [], "flows": []})
    plan["modules"].append({"id": "more", "pages": ["more_a", "more_b"]})
    return plan


def call(llm, purpose):
    return next(c for c in llm.calls if c["purpose"] == purpose)


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_spec(NOTES)

    def test_one_module_makes_two_parallel_calls(self):
        llm = answers()
        files, problems = generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)
        self.assertEqual(set(files), {"backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", "frontend/src/pages/main.jsx"})
        self.assertEqual((len(llm.calls), problems), (2, []))
        frontend_call = call(llm, "frontend:main")
        self.assertIn('import { PageHome, PageNotes, PageNoteNew, PageNote } from "./pages/main.jsx";', frontend_call["user"])
        self.assertIn("PageHome (page home, route /), PageNotes (page notes, route /notes)", frontend_call["user"])
        self.assertEqual(files["frontend/src/pages/main.jsx"], golden_files()["frontend/src/pages/main.jsx"])
        self.assertEqual((call(llm, "backend")["max_tokens"], frontend_call["max_tokens"], frontend_call["temperature"]),
                         (config.MAX_TOKENS["backend"], config.MAX_TOKENS["frontend"], 0.2))

    def test_prompts_share_the_context_prefix(self):
        llm = answers()
        generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)
        prefix = build_context(self.spec, "", golden_plan())
        for c in llm.calls:
            self.assertTrue(c["user"].startswith(prefix + "\n\n")); self.assertEqual(c["system"], load_prompt("rules"))
        self.assertEqual(call(llm, "backend")["user"], prefix + "\n\n" + load_prompt("backend"))

    def test_extra_files_in_an_answer_are_ignored(self):
        llm = answers(backend=[pick("backend/app.js", **{"frontend/src/pages/main.jsx": "x\n"})])
        files, _ = generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)
        self.assertEqual(files["frontend/src/pages/main.jsx"], golden_files()["frontend/src/pages/main.jsx"])

    def test_more_modules_wait_for_the_shell(self):
        more = as_blocks({"frontend/src/pages/more.jsx": stub_module("more", ["PageMoreA", "PageMoreB"])})
        llm = answers(**{"frontend:more": [more]})
        files, problems = generate_app(self.spec, "", ten_page_plan(), llm, temperature=0.2)
        order = [c["purpose"] for c in llm.calls]
        self.assertGreater(order.index("frontend:more"), order.index("frontend:main"))
        self.assertIn(golden_files()["frontend/src/shared.jsx"], call(llm, "frontend:more")["user"])
        self.assertIn(golden_files()["frontend/src/App.jsx"], call(llm, "frontend:more")["user"])
        self.assertIn("PageMoreA (page more_a, route /more_a), PageMoreB (page more_b, route /more_b)", call(llm, "frontend:more")["user"])
        self.assertIn("frontend/src/pages/more.jsx", files); self.assertEqual(problems, [])

    def test_missing_file_is_asked_for_once(self):
        llm = answers(**{"frontend:main-again": [pick(*SHELL, "frontend/src/pages/main.jsx")],
                         "frontend:main": [pick("frontend/src/shared.jsx", "frontend/src/pages/main.jsx")]})
        files, problems = generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)
        self.assertEqual([c["purpose"] for c in llm.calls if c["purpose"].startswith("frontend")], ["frontend:main", "frontend:main-again"])
        self.assertIn("Your previous answer was missing these file blocks: frontend/src/App.jsx.", call(llm, "frontend:main-again")["user"])
        self.assertEqual(files["frontend/src/App.jsx"], golden_files()["frontend/src/App.jsx"]); self.assertEqual(problems, [])

    def test_missing_extra_module_gets_a_stub(self):
        llm = answers(**{"frontend:more": ["no blocks here", "still none"]})
        files, problems = generate_app(self.spec, "", ten_page_plan(), llm, temperature=0.2)
        self.assertEqual(files["frontend/src/pages/more.jsx"], stub_module("more", ["PageMoreA", "PageMoreB"]))
        self.assertIn("module more was not generated; a stub was written", problems)

    def test_failed_extra_module_call_gets_a_stub(self):
        llm = answers(**{"frontend:more": [LLMError("busy")]})
        files, problems = generate_app(self.spec, "", ten_page_plan(), llm, temperature=0.2)
        self.assertEqual(files["frontend/src/pages/more.jsx"], stub_module("more", ["PageMoreA", "PageMoreB"]))
        self.assertIn("module more was not generated; a stub was written", problems)

    def test_missing_shell_raises(self):
        llm = answers(**{"frontend:main": [pick("frontend/src/pages/main.jsx")] * 2})
        with self.assertRaises(GenerationError): generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)

    def test_missing_backend_raises(self):
        llm = answers(backend=["nothing", "nothing"])
        with self.assertRaises(GenerationError): generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)

    def test_backend_model_failure_raises(self):
        llm = answers(backend=[LLMError("busy")])
        with self.assertRaises(GenerationError): generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)

    def test_a_complete_second_answer_replaces_the_first(self):
        # v3 Ctrip: main.jsx from the second answer imported isValidId from the second answer's shared.jsx, but the first
        # answer's shared.jsx was kept, so the build failed (docs/5-arcflash-v4/plan.md R4).
        second_shared = golden_files()["frontend/src/shared.jsx"] + "\nexport function isValidId(id) { return !!id; }\n"
        llm = answers(**{"frontend:main-again": [pick("frontend/src/App.jsx", "frontend/src/pages/main.jsx",
                                                      **{"frontend/src/shared.jsx": second_shared})],
                         "frontend:main": [pick(*SHELL)]})
        files, problems = generate_app(self.spec, "", golden_plan(), llm, temperature=0.2)
        self.assertEqual(files["frontend/src/shared.jsx"], second_shared); self.assertEqual(problems, [])

    def test_tests_chunk_by_twenty_and_see_only_the_digest(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        data = yaml.safe_load((NOTES / "requirements.yaml").read_text())
        leaf = data["children"][0]["children"][1]
        template = leaf["scenarios"][0]
        leaf["scenarios"] = [dict(template, id=f"SCN-REQ-1.2-{i:02d}") for i in range(1, 44)]
        (tmp / "requirements.yaml").write_text(yaml.safe_dump(data))
        (tmp / "reference").mkdir(); shutil.copy(NOTES / "reference" / "notes.png", tmp / "reference")
        spec = load_spec(tmp)
        ids = [s.id for s in spec.scenarios]
        self.assertEqual(len(ids), 45)
        llm = FakeLLM({f"tests:{n}": [as_blocks({f"tests/scenarios-{n}.spec.ts": f"// {n}\n"})] for n in (1, 2, 3)})
        files, problems = write_tests(spec, llm)
        self.assertEqual(sorted(c["purpose"] for c in llm.calls), ["tests:1", "tests:2", "tests:3"])
        self.assertIn(", ".join(ids[:20]), call(llm, "tests:1")["user"])
        self.assertIn(", ".join(ids[40:]), call(llm, "tests:3")["user"])
        self.assertIn("=== FILE: tests/scenarios-3.spec.ts ===", call(llm, "tests:3")["user"])
        for c in llm.calls: self.assertNotIn("\nPLAN\n", c["user"]); self.assertNotIn("SCREENSHOT NOTES", c["user"])
        self.assertEqual((set(files), problems), ({"tests/scenarios-1.spec.ts", "tests/scenarios-2.spec.ts", "tests/scenarios-3.spec.ts"}, []))
        self.assertTrue(call(llm, "tests:1")["user"].startswith(build_context(spec) + "\n\n"))
        self.assertEqual(call(llm, "tests:1")["max_tokens"], config.MAX_TOKENS["tests"])
        self.assertTrue(all(c["background"] for c in llm.calls))   # the test writer never takes a plan or code slot

    def test_unwritten_tests_are_a_problem(self):
        llm = FakeLLM({"tests:1": ["nothing", LLMError("busy")]})
        files, problems = write_tests(self.spec, llm)
        self.assertEqual((files, problems), ({}, ["tests for scenarios SCN-REQ-1.1-01 to SCN-REQ-1.3-01 were not written"]))

    def test_module_imports_and_components(self):
        self.assertEqual(module_imports(ten_page_plan()).splitlines()[1], 'import { PageMoreA, PageMoreB } from "./pages/more.jsx";')
        self.assertEqual(components_text(golden_plan(), "main"),
                         "PageHome (page home, route /), PageNotes (page notes, route /notes), "
                         "PageNoteNew (page note_new, route /notes/new), PageNote (page note, route /notes/:id)")

    def test_stub_module_exports_every_component(self):
        s = stub_module("more", ["PageA", "PageB"])
        self.assertIn("export function PageA()", s); self.assertIn("export function PageB()", s); self.assertIn("<h1>Not built</h1>", s)


if __name__ == "__main__":
    unittest.main()
