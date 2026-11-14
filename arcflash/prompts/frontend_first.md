Task: write the frontend shell and the pages of module "{{module}}" for the plan above.

Fixed files you use but do not write:
- src/main.jsx renders <BrowserRouter><App /></BrowserRouter> and imports src/index.css, which loads Tailwind.
- src/api.js exports api with get(path), post(path, body), put(path, body), patch(path, body) and del(path). Each returns a promise of {ok, status, data}, where data is the parsed JSON body.

Write three files:
1. frontend/src/shared.jsx: export AppProvider (state shared across pages; when the plan has accounts it loads GET /api/session once and exposes user, setUser and ready), export useApp(), export Layout({ children }), which renders layout.controls once and then children, and any small shared components the pages need.
2. frontend/src/App.jsx: export default function App(), returning <AppProvider> around <Routes> with one <Route> per planned page. The element is the page component, wrapped in <Layout> when the page has "layout": true. Add <Route path="*"> showing a "Page not found" heading. Import the page components with exactly these lines:
{{imports}}
3. frontend/src/pages/{{module}}.jsx: export one function component per page: {{components}}. Implement every requirement listed on these pages completely: every control, state, message and flow in the requirement text and its scenarios.

Pages import { api } from "../api.js", what they need from "../shared.jsx", and { Link, useNavigate, useParams } from "react-router-dom". Style with Tailwind classes, following the screenshot notes. Render a control with "in" inside the element of the area it names, never beside it.

Output the three file blocks in this order and nothing else:
=== FILE: frontend/src/shared.jsx ===
(the code)
=== END FILE ===
=== FILE: frontend/src/App.jsx ===
(the code)
=== END FILE ===
=== FILE: frontend/src/pages/{{module}}.jsx ===
(the code)
=== END FILE ===
