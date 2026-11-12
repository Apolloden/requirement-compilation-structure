// Fixed server: database, JSON bodies, cookies, health check, the generated API, the built frontend, PORT.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import Database from "better-sqlite3";
import { registerApi } from "./app.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const dbFile = process.env.DATABASE_URL || path.join(here, "data", "app.db");
if (dbFile !== ":memory:") fs.mkdirSync(path.dirname(path.resolve(dbFile)), { recursive: true });
const db = new Database(dbFile);
db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");

function parseCookies(header) {
  const cookies = {};
  for (const part of (header || "").split(";")) {
    const at = part.indexOf("=");
    if (at < 0) continue;
    const name = part.slice(0, at).trim();
    if (!name) continue;
    const raw = part.slice(at + 1).trim();
    try {
      cookies[name] = decodeURIComponent(raw);
    } catch {
      cookies[name] = raw;
    }
  }
  return cookies;
}

const app = express();
app.use(express.json({ limit: "5mb" }));
app.use((req, _res, next) => {
  req.cookies = parseCookies(req.headers.cookie);
  next();
});
app.get("/api/health", (_req, res) => res.json({ status: "ok" }));

registerApi(app, db);

app.use("/api", (_req, res) => res.status(404).json({ error: "Unknown API route" }));

const dist = path.join(here, "..", "frontend", "dist");
if (fs.existsSync(dist)) {
  app.use(express.static(dist));
  app.get("*", (_req, res) => res.sendFile(path.join(dist, "index.html")));
}

app.use((err, _req, res, _next) => {
  console.error(err);
  if (res.headersSent) return;
  res.status(500).json({ error: "Internal error" });
});

const port = Number(process.env.PORT || 3000);
app.listen(port, () => console.log(`[server] listening on port ${port}`));
