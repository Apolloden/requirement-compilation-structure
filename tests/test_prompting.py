import json
import re
import unittest
from pathlib import Path

from arcflash.prompting import PROMPT_NAMES, build_context, fill, load_prompt, notes_block
from arcflash.spec import load_spec
from tests.golden import golden_plan
from tests.support import AGENT_ROOT, FIXTURES

NOTES = FIXTURES / "specs" / "notes"
PLAN_DOC = AGENT_ROOT.parent / "docs" / "3-arcflash" / "arcflash-plan.md"
AMENDED = {"rules", "tests", "plan", "repair"}   # changed on evidence; see docs/3-arcflash/arcflash-changes.md and docs/6-arclaser/arclaser-changes.md
ADDED = {"kept_files", "quick_fix"}    # added after the blueprint (docs/5-arcflash-v4/plan.md, review point 3; ArcLaser Task 5)


class PromptTests(unittest.TestCase):
    def test_every_prompt_exists_and_starts_as_in_appendix_a(self):
        self.assertTrue(load_prompt("rules").startswith("You write complete, working web applications"))
        self.assertIn('uniqueName("<the name the scenario uses>")', load_prompt("tests"))
        self.assertIn("{{problems}}", load_prompt("repair")); self.assertIn("{{imports}}", load_prompt("frontend_first"))
        for name in PROMPT_NAMES: self.assertTrue(load_prompt(name).strip(), name)

    def test_amendments_after_the_paid_checks(self):
        # P2 (docs/3-arcflash/arcflash-changes.md): uniqueName values broke a username and an email field; native form validation
        # blocked a submit without any visible message.
        self.assertIn("<form noValidate>", load_prompt("rules"))
        self.assertIn('uniqueId("<base>")', load_prompt("tests"))
        self.assertIn("uniqueName, uniqueId, invalidateSession", load_prompt("tests"))

    def test_rules_cover_every_role_the_specs_use(self):
        # ARC-Bench run 2026-11-13 (docs/4-arcflash-update/evidence.md C1-C3): roles the spec states that the rules had no words for.
        rules = load_prompt("rules")
        for markup in ('<nav aria-label=', '<section aria-label=', '<article aria-label=', '<tr aria-label=',
                       '<li aria-label=', 'role="group"', 'role="status"', 'role="timer"',
                       'type="search"', 'type="number"', 'role="tabpanel"'):
            self.assertIn(markup, rules)
        self.assertIn("16. Menus.", rules)
        self.assertIn("placeholder", rules.split("4. Choices.")[0])   # rule 3 sets placeholders
        self.assertLess(len(rules), 5233 + 1500)                       # rules.md was 5,233 characters; see the update plan's constraints
        self.assertIn("use menuitem, tab, option or switch only where the requirements state that role", load_prompt("plan"))

    @unittest.skipUnless(PLAN_DOC.is_file(), "plan document not next to the agent folder")
    def test_prompts_equal_appendix_a_exactly(self):
        text = PLAN_DOC.read_text(encoding="utf-8")
        appendix = text[text.index("# Appendix A: prompt texts"):text.index("# Appendix B: formats")]
        found = dict(re.findall(r"### A\.\d+ `([a-z_]+)\.md`[^\n]*\n\n```\n(.*?)\n```\n", appendix, re.S))
        self.assertEqual(set(found), set(PROMPT_NAMES) - ADDED)
        for name in PROMPT_NAMES:
            if name not in AMENDED | ADDED:
                self.assertEqual(load_prompt(name), found[name], name)

    def test_helpers_export_every_role_helper(self):
        # evidence.md C1: with only region() available, our tests looked up a navigation landmark as a region, like the code.
        helpers = (AGENT_ROOT / "arcflash" / "template" / "harness" / "tests" / "helpers.ts").read_text(encoding="utf-8")
        for export in ("export const navigation", "export const row", "export const listitem", "export const article",
                       "export const group", "export const status", "export const searchbox", "export const spinbutton",
                       "export const tab ", "export const tabpanel", "export const menuitem", "export function role(",
                       "export function placeholder("):
            self.assertIn(export, helpers)
        tests = load_prompt("tests")
        self.assertIn("navigation", tests); self.assertIn("placeholder(", tests)
        self.assertIn("never substitute region for another role", tests)

    def test_a_missing_element_fails_a_step_after_10_seconds(self):
        # Like the grader (evidence.md section 2.3; the calibrated grader-style suites use actionTimeout 10000). With only the
        # 30 s test timeout, 137 failing tests held the Ctrip check for 1,195 s and no repair round fit in the time budget.
        config = (AGENT_ROOT / "arcflash" / "template" / "harness" / "playwright.config.ts").read_text(encoding="utf-8")
        self.assertIn("actionTimeout: 10000", config)
        self.assertIn("timeout: 30000", config)   # a long test that passes keeps its time

    def test_fill(self):
        self.assertEqual(fill("a {{x}} {y} {{x}}", x="1"), "a 1 {y} 1")
        with self.assertRaises(KeyError): fill("{{missing}}")
        self.assertEqual(fill("{{a}}", a="{{b}}"), "{{b}}")  # one pass

    def test_context_prefix_is_stable(self):
        spec = load_spec(NOTES); notes = notes_block(spec, {"reference/notes.png": "Layout: a list."})
        t, p, c = build_context(spec), build_context(spec, notes), build_context(spec, notes, golden_plan())
        self.assertTrue(p.startswith(t)); self.assertTrue(c.startswith(p))
        self.assertIn("PLAN\n" + json.dumps(golden_plan(), ensure_ascii=False, indent=1), c)
        self.assertTrue(t.startswith("APPLICATION: Notes"))
        self.assertIn(spec.digest.rstrip("\n") + "\n\nSCREENSHOT NOTES\n", p)

    def test_notes_block_format(self):
        spec = load_spec(NOTES)
        notes = notes_block(spec, {"reference/notes.png": "Layout: a list."})
        self.assertTrue(notes.startswith("SCREENSHOT NOTES\nLayout and style only. The requirement text decides controls and names.\n"))
        self.assertIn("IMG-NOTES, reference/notes.png (page notes)\nLayout: a list.", notes)
        self.assertIn("(screenshot unavailable)", notes_block(spec, {}))

    def test_notes_block_is_empty_without_images(self):
        spec = load_spec(NOTES); spec.images = []
        self.assertEqual(notes_block(spec, {}), "")


if __name__ == "__main__":
    unittest.main()
