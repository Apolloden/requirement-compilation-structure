Task: write the plan of the whole application as one JSON object. The backend, the frontend and the checks are built from this plan in parallel, so it must cover every leaf requirement and use the exact names from the requirement text.

Shape, with example values to replace:
{
  "app": {"title": "...", "notes": ["facts every part needs, copied from the requirements: clock, time zone, seeded accounts, fixed data"]},
  "auth": {"accounts": false},
  "tables": [{"name": "items", "columns": ["id INTEGER PRIMARY KEY AUTOINCREMENT", "name TEXT NOT NULL"]}],
  "seed": [{"table": "items", "rows": [{"name": "..."}], "source": "the requirement or scenario ID that says these rows exist"}],
  "api": [{"method": "GET", "path": "/api/items/:id", "request": {}, "response": "{\"item\": {\"id\": 1, \"name\": \"...\"}}", "errors": ["404 {\"error\": \"...\"}"], "reqs": ["REQ-..."]}],
  "layout": {"controls": [{"role": "link", "name": "...", "to": "/...", "when": "always"}]},
  "pages": [{"id": "item_detail", "route": "/items/:id", "title": "...", "layout": true, "module": "main", "reqs": ["REQ-..."],
             "controls": [{"role": "button", "name": "...", "when": "always"}],
             "shows": ["the item's name as an h1"], "flows": ["Save: PUT /api/items/:id, then open /items/:id"]}],
  "modules": [{"id": "main", "pages": ["item_detail"]}]
}

Rules:
- auth is {"accounts": true} only when the requirements have sign-in; then api includes GET /api/session.
- pages: one entry per URL. A form that a control opens gets its own route, for example /items/new or /items/:id/edit, unless the requirements describe it as a dialog. Page ids are lowercase snake_case.
- Every leaf requirement ID is in the "reqs" of each page its scenarios start on, act on or return to.
- controls: every element the requirement text names with a fixed name (links, buttons, fields, headings, regions, dialogs, alerts, checkboxes, radio groups, tabs), with its ARIA role chosen by the rules above and its name copied exactly from the text. "when" is "always", "signed_in", "signed_out" or a few words such as "after Delete". A name filled in at runtime, such as a record's name, goes in "shows", not in controls. An element the text names only by its role, such as an error with role=alert, is listed with "name": "". An action the requirements name without a role is a link or a button; use menuitem, tab, option or switch only where the requirements state that role. A control the text places inside a named region, form, dialog, navigation or row ("X displays Y", "Y in X", "within X") gets "in": "X", and X is a control too.
- layout.controls: the shared header and navigation, rendered once around every page that has "layout": true. A layout control is never repeated in a page's controls.
- No page has two controls with the same role and name, counting the layout.
- api: every read and write the pages need, as REST paths under /api with numeric ids written :id.
- seed: only records the requirements say exist before a scenario starts. Write passwords in plain text here; the backend stores their hashes.
- modules: a single module "main" when there are at most 8 pages; otherwise group related pages, at most 8 pages per module, with the home page in the first module. Module ids are lowercase snake_case.
- The requirement text decides names and behavior. The screenshot notes add layout hints only.
