Task: write the frontend shell for the plan above: the two shared files every page module uses. The page modules are written next, in parallel, against these files.

Fixed files you use but do not write:
- src/main.jsx renders <BrowserRouter><App /></BrowserRouter> and imports src/index.css, which loads Tailwind.
- src/api.js exports api with get(path), post(path, body), put(path, body), patch(path, body) and del(path). Each returns a promise of {ok, status, data}, where data is the parsed JSON body.

Write two files:
1. frontend/src/shared.jsx: export AppProvider (state shared across pages; when the plan has accounts it loads GET /api/session once and exposes user, setUser and ready), export useApp(), export Layout({ children }), which renders layout.controls once (a control with "to" opens that route when clicked: a <Link>, or a button that calls useNavigate) and then children, and small shared components that several pages need, each with a one-line comment on how to use it.
2. frontend/src/App.jsx: export default function App(), returning <AppProvider> around <Routes> with one <Route> per planned page. The element is the page component, wrapped in <Layout> when the page has "layout": true. Add <Route path="*"> showing a "Page not found" heading. Import the page components with exactly these lines:
{{imports}}

Style with Tailwind classes, following the screenshot notes. Render a control with "in" inside the element of the area it names, never beside it.

Output the two file blocks in this order and nothing else:
=== FILE: frontend/src/shared.jsx ===
(the code)
=== END FILE ===
=== FILE: frontend/src/App.jsx ===
(the code)
=== END FILE ===
