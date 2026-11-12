"""The contract check inside the agent: the real lite design passes, and every defect that
would crash or mislead a build stage is reported in plain language."""

import unittest

from infrastructure.stages.design.contract import check_artifacts, check_design
from design_fixtures import LITE, artifacts


class ContractCheck(unittest.TestCase):
    def problems_after(self, name, change):
        data = artifacts()
        if name:
            change(data[name])
        return check_artifacts(**data)

    def assertCaught(self, name, change, expected):
        problems = self.problems_after(name, change)
        self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_the_real_lite_design_passes(self):
        self.assertEqual(self.problems_after(None, None), [])

    def test_check_design_reads_a_directory(self):
        self.assertEqual(check_design(LITE), [])

    # frontend_triggers.json: the two defects that crash all of stage 10 today
    def test_missing_leaf_entry_in_triggers(self):
        self.assertCaught("triggers", lambda d: d["requirements"].pop("REQ-2.3"), "no entry for REQ-2.3")

    def test_dangling_trigger_id(self):
        self.assertCaught("triggers", lambda d: d["requirements"]["REQ-2.3"].append("TR-GHOST"),
            "unknown trigger TR-GHOST")

    def test_trigger_calls_unknown_api(self):
        def change(d):
            trigger = next(t for t in d["triggers"].values() if any(a["kind"] == "api" for a in t["action"]))
            next(a for a in trigger["action"] if a["kind"] == "api")["api"] = "API-ghost"
        self.assertCaught("triggers", change, "unknown API 'API-ghost'")

    def test_trigger_source_is_not_an_element(self):
        def change(d):
            next(iter(d["triggers"].values()))["source"] = "UI-ghost"
        self.assertCaught("triggers", change, "source 'UI-ghost' is not a UI element")

    # ui_design.json: nothing is checked today
    def test_page_root_missing(self):
        self.assertCaught("ui", lambda d: d["pages"]["home"].update(root="UI-ghost"),
            "root 'UI-ghost' is not an element")

    def test_parent_chain_broken(self):
        self.assertCaught("ui", lambda d: d["elements"]["UI-search"].update(parent="UI-ghost"),
            "parent chain reaches unknown element UI-ghost")

    def test_unknown_dtype(self):
        self.assertCaught("ui", lambda d: d["elements"]["UI-search"]["data"][0].update(dtype="text"),
            "dtype 'text'")

    def test_unknown_image(self):
        self.assertCaught("ui", lambda d: d["pages"]["home"]["images"].append("IMG-GHOST"),
            "unknown image IMG-GHOST")

    def test_leaf_without_ui(self):
        self.assertCaught("ui", lambda d: d["requirements"].update({"REQ-2.5": []}),
            "requirements[REQ-2.5]: no UI elements")

    def test_two_pages_same_route(self):
        def change(d):
            d["pages"]["copy"] = {**d["pages"]["home"], "name": "Copy"}
        self.assertCaught("ui", change, "same route as home")

    def test_two_links_with_the_same_name_on_one_page(self):
        # A header link next to a breadcrumb link with the same name, at design time.
        def change(d):
            d["elements"]["UI-book-header-books"] = {"type": "link", "parent": d["pages"]["book"]["root"],
                "mode": [], "data": [], "prop": {"label": "Books", "href": "/books"}}
        self.assertCaught("ui", change, "2 links named 'Books'")

    # backend_apis.json
    def test_requirement_lists_unknown_api(self):
        self.assertCaught("backend", lambda d: d["requirements"]["REQ-2.1"].append("API-ghost"),
            "unknown API API-ghost")

    def test_api_misses_a_required_write(self):
        def change(d):
            d["apis"]["API-create-book"]["write"] = []
        self.assertCaught("backend", change, "obligation coverage")

    def test_two_apis_same_route(self):
        def change(d):
            d["apis"]["API-copy"] = {**d["apis"]["API-get-book"]}
        self.assertCaught("backend", change, "same method and path as API-get-book")

    def test_api_missing_keys(self):
        self.assertCaught("backend", lambda d: d["apis"]["API-get-book"].pop("response"),
            "missing ['response']")

    # db_schema.json and db_obligations.json
    def test_bad_field_type_and_reference(self):
        def change(d):
            field = next(iter(d["entities"]["Page"]["fields"].values()))
            field.update(type="varchar", references="Ghost.id")
        problems = self.problems_after("schema", change)
        self.assertTrue(any("type 'varchar'" in p for p in problems), problems)
        self.assertTrue(any("references unknown field 'Ghost.id'" in p for p in problems), problems)

    def test_entity_without_primary_key(self):
        def change(d):
            for field in d["entities"]["Book"]["fields"].values():
                field["primary_key"] = False
        self.assertCaught("schema", change, "Book has no primary key")

    def test_missing_obligations_for_a_leaf(self):
        self.assertCaught("obligations", lambda d: d["requirements"].pop("REQ-3.1"),
            "no record for leaf requirements REQ-3.1")

    def test_one_run_lists_every_problem(self):
        def change(d):
            d["requirements"].pop("REQ-2.3")
            d["requirements"]["REQ-2.4"].append("TR-GHOST")
        problems = self.problems_after("triggers", change)
        self.assertGreaterEqual(len(problems), 2, problems)


if __name__ == "__main__":
    unittest.main()
