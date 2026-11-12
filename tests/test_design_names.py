import unittest
from infrastructure.stages.design.names import element_name, element_role, named_elements
from infrastructure.stages.design.contract import check_artifacts
from design_fixtures import LITE2, artifacts, load

PLACEHOLDER = "Write your page content here..."

def derived_plan(ui):
    """A naming table that lists every fixed-name link and button of the design, no header."""
    naming = {}
    for pid in ui["pages"]:
        for _, role, name in named_elements(ui, pid):
            if role in ("link", "button"):
                naming.setdefault((name, role), []).append(pid)
    return {"pages": {pid: {"layout": False, "requirements": []} for pid in ui["pages"]},
            "layout": {"elements": {}},
            "naming": [{"name": n, "role": r, "pages": p} for (n, r), p in naming.items()]}

class Names(unittest.TestCase):
    def test_roles_and_names(self):
        self.assertEqual(element_role({"type": "link"}), "link")
        self.assertEqual(element_role({"type": "select"}), "combobox")
        self.assertEqual(element_role({"type": "input", "prop": {"input_type": "textarea"}}), "textbox")
        self.assertIsNone(element_role({"type": "input", "prop": {"input_type": "search"}}))
        self.assertIsNone(element_role({"type": "input", "prop": {"input_type": "password"}}))
        self.assertIsNone(element_role({"type": "container", "prop": {"label": "Book list"}}))
        self.assertEqual(element_role({"type": "container", "prop": {"label": "Book list"}}, {"Book list"}), "region")
        self.assertEqual(element_name({"type": "heading", "prop": {"text": "Books"}}), "Books")
        self.assertEqual(element_name({"type": "input", "prop": {"label": "Page content", "placeholder": PLACEHOLDER}}), "Page content")
        self.assertEqual(element_name({"type": "input", "prop": {"placeholder": PLACEHOLDER}}), PLACEHOLDER)
        self.assertIsNone(element_name({"type": "button", "prop": {"label": "{item name}"}}))
        self.assertIsNone(element_name({"type": "button"}))

    def test_first_try_design_passes_with_a_derived_naming_table(self):
        data = artifacts()
        self.assertEqual(check_artifacts(**data, plan=derived_plan(data["ui"])), [])

    def test_second_try_design_reports_the_page_content_label(self):
        data = artifacts(LITE2)
        plan = derived_plan(data["ui"])
        plan["naming"].append({"name": PLACEHOLDER, "role": "textbox", "pages": ["page_form", "page_edit"]})
        problems = check_artifacts(**data, plan=plan)
        for pid in ("page_form", "page_edit"):
            self.assertIn(f"ui_design.pages.{pid}: no textbox named '{PLACEHOLDER}' (naming table)", problems)

    def test_link_missing_from_the_naming_table(self):
        data = artifacts()
        plan = derived_plan(data["ui"])
        plan["naming"] = [entry for entry in plan["naming"] if entry["name"] != "Create New Book"]
        self.assertTrue(any("'Create New Book'" in p and "is not in the naming table" in p
            for p in check_artifacts(**data, plan=plan)))

    def test_header_page_without_header_elements(self):
        data = artifacts()
        plan = derived_plan(data["ui"])
        plan["pages"]["book"]["layout"] = True
        self.assertIn("ui_design.pages.book: page shows the header but has no header elements",
            check_artifacts(**data, plan=plan))

    def test_page_of_a_skipped_leaf_is_exempt_from_missing_names(self):
        data = artifacts(LITE2)
        plan = derived_plan(data["ui"])
        plan["naming"].append({"name": PLACEHOLDER, "role": "textbox", "pages": ["page_form"]})
        plan["pages"]["page_form"]["requirements"] = ["REQ-3.1"]
        problems = check_artifacts(**data, plan=plan, skipped=frozenset({"REQ-3.1"}))
        self.assertFalse(any(PLACEHOLDER in p for p in problems))

class RuntimeNameGuidance(unittest.TestCase):                                 # found in the paid plan-stage run
    def test_unlisted_link_message_says_how_to_mark_a_runtime_name(self):
        data = artifacts()
        plan = derived_plan(data["ui"])
        plan["naming"] = [entry for entry in plan["naming"] if entry["name"] != "Create New Book"]
        message = next(p for p in check_artifacts(**data, plan=plan) if "'Create New Book'" in p)
        self.assertIn("is not in the naming table", message)
        self.assertIn("if its name is filled in at runtime, leave prop.label out and declare the value in data", message)
