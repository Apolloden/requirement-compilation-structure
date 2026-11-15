Task: write backend/app.js, the complete backend for the plan above.

- An ES module that exports function registerApi(app, db). server.js is fixed and not yours: it parses JSON bodies into req.body and cookies into req.cookies, calls registerApi, then serves the frontend and answers unknown /api routes with 404.
- db is a better-sqlite3 Database, whose API is synchronous. registerApi first runs CREATE TABLE IF NOT EXISTS for every planned table, then inserts the seed records inside one transaction when their table is empty. A seeded password is stored as its hash, never as plain text.
- Seed records: every record in the SEED LIST below, each one, with its exact values (emails, usernames, passwords, numbers, names, dates). Scenarios sign in with these accounts, so a missing one fails every scenario that uses it. Records a scenario creates through the UI are not seeded.
- Register every planned API directly on app, with its full path and exact method, for example app.get("/api/items/:id", handler), and with the planned request and response shapes. Handlers are synchronous.
- Validate input. Answer errors as {"error": "message"} with status 400, 401, 404 or 409. Wrap every handler so that an exception answers 500 with {"error": "..."} instead of stopping the server.
- Set a cookie with res.cookie(name, value, { httpOnly: true, sameSite: "lax", path: "/" }) and clear it with res.clearCookie(name, { path: "/" }).
- Passwords: crypto.scryptSync with a random 16-byte salt, stored as "salt:hash" in hex, verified with crypto.timingSafeEqual.

SEED LIST (the "Seed prerequisites" lines of all scenarios, each distinct line once):
{{seeds}}

Output exactly one file block and nothing else:
=== FILE: backend/app.js ===
(the code)
=== END FILE ===
