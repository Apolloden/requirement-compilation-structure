import unittest

from arcflash.check_static import backend_routes, frontend_api_calls, normalize_api_path, static_issues
from tests.golden import golden_files, golden_plan

APP = "backend/app.js"
MAIN = "frontend/src/pages/main.jsx"
ROUTES = "frontend/src/App.jsx"


def changed(key, old, new):
    files = golden_files()
    assert old in files[key], (key, old)
    files[key] = files[key].replace(old, new)
    return files


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


if __name__ == "__main__":
    unittest.main()
