Task: write frontend/src/pages/{{module}}.jsx for the plan above. Export one function component per page: {{components}}. Implement every requirement listed on these pages completely: every control, state, message and flow in the requirement text and its scenarios.

The shell already exists. Use its exports exactly as written:
=== frontend/src/shared.jsx ===
{{shared}}
=== frontend/src/App.jsx ===
{{app}}

Import { api } from "../api.js" (get, post, put, patch and del, each returning {ok, status, data}), what you need from "../shared.jsx", and { Link, useNavigate, useParams } from "react-router-dom". Never render the layout's controls inside a page. A page listed in a plan.shared_navs entry renders that entry's component from the shell and never writes its own copy of that navigation. Render a control with "in" inside the element of the area it names, never beside it. Style with Tailwind classes, following the screenshot notes.

Output exactly one file block and nothing else:
=== FILE: frontend/src/pages/{{module}}.jsx ===
(the code)
=== END FILE ===
