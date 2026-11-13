import unittest

from arcflash.check_static import backend_routes, frontend_api_calls, normalize_api_path, role_issues, static_issues
from tests.golden import golden_files, golden_plan

APP = "backend/app.js"
MAIN = "frontend/src/pages/main.jsx"
ROUTES = "frontend/src/App.jsx"
SHARED = "frontend/src/shared.jsx"


def changed(key, old, new):
    files = golden_files()
    assert old in files[key], (key, old)
    files[key] = files[key].replace(old, new)
    return files


def with_control(role, name):
    plan = golden_plan(); plan["layout"]["controls"].append({"role": role, "name": name, "when": "always"})
    return plan


def role_issues_of(files, plan):
    return [i for i in static_issues(files, plan) if "(rule 6)" in i or " looks up " in i]


class StaticCheckTests(unittest.TestCase):
    def test_golden_app_is_clean(self):
        self.assertEqual(static_issues(golden_files(), golden_plan()), [])

    def test_normalize_api_path(self):
        self.assertEqual(normalize_api_path("/api/notes/${id}/tags?x=1"), "/api/notes/:p/tags")
        self.assertEqual(normalize_api_path("/api/notes/:noteId"), "/api/notes/:p")
        self.assertEqual(normalize_api_path("/api/notes/"), "/api/notes")
        self.assertEqual(normalize_api_path("/"), "/")

    def test_frontend_calls(self):
        src = {"frontend/src/pages/main.jsx": "api.get(`/api/notes/${id}`); api.post(\"/api/notes\", b); api.del('/api/notes/' + id)"}
        self.assertEqual(frontend_api_calls(src), {("GET", "/api/notes/:p"), ("POST", "/api/notes"), ("DELETE", "/api/notes/:p")})

    def test_frontend_calls_read_concatenation_and_queries(self):
        src = {MAIN: 'api.post("/api/notes/" + note.id + "/tags", {}); api.get("/api/search?q=" + encodeURIComponent(q)); '
                     'api.put(`/api/notes/${id}`, b)'}
        self.assertEqual(frontend_api_calls(src), {("POST", "/api/notes/:p/tags"), ("GET", "/api/search"), ("PUT", "/api/notes/:p")})

    def test_backend_routes(self):
        self.assertEqual(backend_routes('app.get("/api/notes/:id", h); app.delete(\'/api/notes/:id\', h)'),
                         {("GET", "/api/notes/:p"), ("DELETE", "/api/notes/:p")})

    def test_each_problem_message(self):
        plan_api_put = golden_plan(); plan_api_put["api"].append({"method": "PUT", "path": "/api/notes/:id"})
        plan_layout = golden_plan(); plan_layout["layout"]["controls"].append({"role": "link", "name": "Archive", "to": "/archive", "when": "always"})
        missing = golden_files(); del missing[ROUTES]
        cases = [
            ("missing file", missing, golden_plan(), "missing file frontend/src/App.jsx"),
            ("no registerApi", changed(APP, "export function registerApi", "function registerApi"), golden_plan(),
             "backend/app.js does not export registerApi"),
            ("no component", changed(MAIN, "export function PageNote(", "function PageNote("), golden_plan(),
             "frontend/src/pages/main.jsx does not export PageNote"),
            ("no route", changed(ROUTES, '"/notes/new"', '"/notes/create"'), golden_plan(),
             'frontend/src/App.jsx has no route "/notes/new"'),
            ("unknown call", changed(MAIN, "  async function remove() {", '  api.post("/api/notes/" + id + "/tags", {});\n  async function remove() {'),
             golden_plan(), "frontend calls POST /api/notes/:p/tags but backend/app.js has no such route"),
            ("plan api missing", golden_files(), plan_api_put, "plan API PUT /api/notes/:id is not implemented in backend/app.js"),
            ("no app routes", changed(APP, "app.", "router."), golden_plan(),
             'backend/app.js registers no app.<method>("/api/...") routes; register every route on app with its full path'),
            ("page control", changed(MAIN, 'aria-label="Note list"', 'aria-label="Notes list"'), golden_plan(),
             'region "Note list" (page notes) does not appear in the frontend code'),
            ("layout control", golden_files(), plan_layout, 'link "Archive" (layout) does not appear in the frontend code'),
            ("window.confirm", changed(MAIN, "  async function remove() {", '  if (window.confirm("x")) {}\n  async function remove() {'),
             golden_plan(), "frontend/src/pages/main.jsx uses window.confirm; use an in-page role=dialog or role=alert instead"),
            ("bare alert", changed(MAIN, "  async function remove() {", '  alert("x");\n  async function remove() {'),
             golden_plan(), "frontend/src/pages/main.jsx uses window.alert; use an in-page role=dialog or role=alert instead"),
            ("date input", changed(MAIN, '<input id="note-title" type="text"', '<input id="note-title" type="date"'), golden_plan(),
             'frontend/src/pages/main.jsx uses an input of type "date"; use type="text" (rule 3)'),
            ("uuid import", changed(APP, "const CLOCK_START", 'import { v4 } from "uuid";\nconst CLOCK_START'), golden_plan(),
             'backend/app.js imports "uuid", which is not installed; use only the fixed stack'),
            ("backend package in frontend", changed(MAIN, 'import { api } from "../api.js";', 'import { api } from "../api.js";\nimport express from "express";'),
             golden_plan(), 'frontend/src/pages/main.jsx imports "express", which is not installed; use only the fixed stack'),
        ]
        for name, files, plan, expected in cases:
            with self.subTest(name):
                self.assertIn(expected, static_issues(files, plan))

    def test_layout_link_repeated_in_a_page_file(self):
        # Seen in both BookStack-lite runs: the header link again as a breadcrumb on detail pages, which the page
        # check never opens because their routes have parameters.
        files = changed(MAIN, "<article className=\"space-y-3\">", "<article className=\"space-y-3\">\n      <Link to=\"/notes\">\n        Notes\n      </Link>")
        self.assertIn('frontend/src/pages/main.jsx repeats the layout link "Notes"; the layout already shows it on every page (rule 5)',
                      static_issues(files, golden_plan()))
        self.assertEqual(static_issues(golden_files(), golden_plan()), [])   # the heading "Notes" is not a link

    def test_layout_button_repeated_in_a_page_file(self):
        plan = golden_plan(); plan["layout"]["controls"].append({"role": "button", "name": "Delete", "when": "always"})
        self.assertIn('frontend/src/pages/main.jsx repeats the layout button "Delete"; the layout already shows it on every page (rule 5)',
                      static_issues(golden_files(), plan))

    def test_form_without_novalidate(self):
        # P2: the browser's own validation blocked a submit silently; role-based tests never see its bubbles.
        files = changed(MAIN, "<form noValidate onSubmit={save}", "<form onSubmit={save}")
        self.assertIn('frontend/src/pages/main.jsx has a <form> without noValidate; add noValidate and show problems with role="alert" (rule 3)',
                      static_issues(files, golden_plan()))

    def test_no_app_routes_skips_route_comparison(self):
        issues = static_issues(changed(APP, "app.", "router."), golden_plan())
        self.assertFalse([i for i in issues if i.startswith(("frontend calls", "plan API"))], issues)

    def test_name_inside_an_identifier_does_not_count(self):
        files = changed(MAIN, '<label htmlFor="note-body">Body</label>', '<label htmlFor="note-body">Text</label>')
        self.assertIn("setBody", files[MAIN])
        self.assertIn('textbox "Body" (page note_new) does not appear in the frontend code', static_issues(files, golden_plan()))

    def test_allowed_imports_are_clean(self):
        files = changed(APP, "const CLOCK_START", 'import crypto from "node:crypto";\nimport fs from "fs";\nconst CLOCK_START')
        self.assertEqual(static_issues(files, golden_plan()), [])

    def test_unicode_control_names_are_found(self):
        plan = golden_plan(); plan["pages"][1]["controls"].append({"role": "button", "name": "下一步", "when": "always"})
        self.assertIn('button "下一步" (page notes) does not appear in the frontend code', static_issues(golden_files(), plan))
        files = golden_files(); files["frontend/src/pages/main.jsx"] += "\nconst label = \"下一步\";\n"
        self.assertNotIn('button "下一步" (page notes) does not appear in the frontend code', static_issues(files, plan))


class RoleCheckTests(unittest.TestCase):
    # evidence.md C1: "Global navigation"(role=navigation) was planned right but rendered as <section aria-label>, a region,
    # and our own test looked it up as a region too.
    def test_named_element_with_the_wrong_role(self):
        files = changed(SHARED, "<nav ", '<section aria-label="Main menu" ')   # a text check does not care that </nav> stays
        self.assertIn('navigation "Main menu" is rendered as <section> (region) in frontend/src/shared.jsx; '
                      'use <nav aria-label="Main menu"> (rule 6)', static_issues(files, with_control("navigation", "Main menu")))

    def test_right_role_is_clean(self):
        files = changed(SHARED, "<nav ", '<nav aria-label="Main menu" ')
        self.assertEqual(role_issues_of(files, with_control("navigation", "Main menu")), [])

    def test_tests_look_up_a_planned_name_with_another_role(self):
        files = changed(SHARED, "<nav ", '<nav aria-label="Main menu" ')
        files["tests/scenarios-9.spec.ts"] = 'await expect(region(page, "Main menu")).toBeVisible();\n'
        self.assertIn('tests/scenarios-9.spec.ts looks up "Main menu" as region; the plan and the requirements say navigation',
                      static_issues(files, with_control("navigation", "Main menu")))

    def test_literal_expression_and_special_characters(self):
        name = 'Record "A" [x-w0] (1)'
        files = changed(SHARED, "<nav ", '<ul><li aria-label={"Record \\"A\\" [x-w0] (1)"}>A</li></ul><nav ')
        self.assertEqual(role_issues_of(files, with_control("listitem", name)), [])
        wrong = changed(SHARED, "<nav ", '<div aria-label={"Record \\"A\\" [x-w0] (1)"}>A</div><nav ')
        self.assertEqual(len(role_issues_of(wrong, with_control("listitem", name))), 1)

    def test_same_name_two_roles_is_fine(self):
        plan = with_control("region", "Add note"); plan["layout"]["controls"].append({"role": "button", "name": "Add note", "when": "always"})
        files = changed(SHARED, "<nav ", '<button>Add note</button><section aria-label="Add note"></section><nav ')
        self.assertEqual(role_issues_of(files, plan), [])

    def test_explicit_role_attribute_decides(self):
        files = changed(SHARED, "<nav ", '<div role="status" aria-label="Saved" ></div><nav ')
        self.assertEqual(role_issues_of(files, with_control("status", "Saved")), [])
        self.assertEqual(role_issues_of(files, with_control("timer", "Saved")),
                         ['timer "Saved" is rendered as <div> (status) in frontend/src/shared.jsx; use <div role="timer" aria-label="Saved"> (rule 6)'])

    def test_role_written_after_the_label_counts(self):
        files = changed(SHARED, "<nav ", '<div aria-label="Saved" role="status"></div><nav ')
        self.assertEqual(role_issues_of(files, with_control("status", "Saved")), [])

    def test_method_named_like_a_helper_is_not_a_lookup(self):
        files = golden_files(); files["tests/scenarios-9.spec.ts"] = 'await response.status(page, "Note list");\n'
        self.assertEqual(role_issues_of(files, golden_plan()), [])
        files["tests/scenarios-9.spec.ts"] = 'await expect(status(page, "Note list")).toBeVisible();\n'
        self.assertEqual(role_issues_of(files, golden_plan()),
                         ['tests/scenarios-9.spec.ts looks up "Note list" as status; the plan and the requirements say region'])

    def test_role_issues_directly(self):
        files = {SHARED: '<section aria-label="Main menu"></section>'}
        self.assertEqual(len(role_issues(files, [{"role": "navigation", "name": "Main menu"}])), 1)
        self.assertEqual(role_issues(files, [{"role": "region", "name": "Main menu"}]), [])
        self.assertEqual(role_issues(files, [{"role": "navigation", "name": "Elsewhere"}]), [])   # no hit: rule 8's problem


if __name__ == "__main__":
    unittest.main()
