import unittest
from infrastructure.stages.frontend.ui.design import normalize_design
from infrastructure.stages.frontend.ui.testing import existence_suite
from design_fixtures import answer, lite_tree, load

def design():
    return normalize_design(load("expected/ui_design.json"), lite_tree())

class StrictNames(unittest.TestCase):
    def test_without_a_plan_nothing_changes(self):
        suite = existence_suite(design(), "books")
        self.assertNotIn("getByRole", suite)
        self.assertEqual(suite, existence_suite(design(), "books", None))

    def test_named_controls_get_one_strict_assertion(self):
        suite = existence_suite(design(), "books", answer("p2"))
        self.assertIn('await expect(page.getByRole("link", { name: "Create New Book", exact: true })).toHaveCount(1);', suite)
        self.assertIn('getByRole("heading"', suite)
        self.assertNotIn("{item name}", suite)

    def test_placeholder_named_field_is_looked_up_by_its_placeholder(self):
        suite = existence_suite(design(), "page_form", answer("p2"))
        self.assertIn('getByRole("textbox", { name: "Write your page content here...", exact: true })', suite)

    def test_region_only_when_the_naming_table_lists_it(self):
        plan = answer("p2")
        self.assertNotIn('getByRole("region"', existence_suite(design(), "books", plan))
        plan["naming"].append({"name": "Book list", "role": "region", "pages": ["books"]})
        self.assertIn('getByRole("region", { name: "Book list", exact: true })', existence_suite(design(), "books", plan))

    def test_quotes_and_non_ascii_names_are_valid_typescript_strings(self):       # Review Focus 1
        ui = design()
        ui["elements"]["UI-books-odd"] = {"type": "button", "parent": "UI-books", "mode": [], "data": [],
            "prop": {"label": 'Say "hi" 上海\\'}}
        suite = existence_suite(ui, "books", answer("p2"))
        self.assertIn('{ name: "Say \\"hi\\" 上海\\\\", exact: true }', suite)

    def test_same_name_twice_is_asserted_once_per_display(self):
        ui = design()
        ui["elements"]["UI-books-second"] = {"type": "link", "parent": "UI-books", "mode": [], "data": [],
            "prop": {"label": "Create New Book", "href": "/books/create"}}
        suite = existence_suite(ui, "books", answer("p2"))
        tests = suite.count("test(")
        self.assertEqual(suite.count('{ name: "Create New Book", exact: true }'), tests)
