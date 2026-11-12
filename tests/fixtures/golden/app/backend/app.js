// Golden backend: the notes API of the golden plan, written by hand to the rules of Appendix A.1.
const CLOCK_START = Date.parse("2026-01-01T09:00:00Z");
const SERVER_START = Date.now();

function now() {
  return new Date(CLOCK_START + (Date.now() - SERVER_START)).toISOString();
}

function safe(handler) {
  return (req, res) => {
    try {
      handler(req, res);
    } catch (error) {
      console.error(error);
      res.status(500).json({ error: "Internal error" });
    }
  };
}

export function registerApi(app, db) {
  db.exec(`CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
  )`);

  const findNote = db.prepare("SELECT id, title, body, created_at FROM notes WHERE id = ?");

  app.get("/api/notes", safe((req, res) => {
    const notes = db.prepare("SELECT id, title FROM notes ORDER BY id").all();
    res.json({ notes });
  }));

  app.post("/api/notes", safe((req, res) => {
    const title = typeof req.body?.title === "string" ? req.body.title : "";
    const body = typeof req.body?.body === "string" ? req.body.body : "";
    if (!title.trim()) return res.status(400).json({ error: "Title is required" });
    const info = db.prepare("INSERT INTO notes (title, body, created_at) VALUES (?, ?, ?)").run(title, body, now());
    res.status(201).json({ note: findNote.get(info.lastInsertRowid) });
  }));

  app.get("/api/notes/:id", safe((req, res) => {
    const note = findNote.get(Number(req.params.id));
    if (!note) return res.status(404).json({ error: "Note not found" });
    res.json({ note });
  }));

  app.delete("/api/notes/:id", safe((req, res) => {
    const info = db.prepare("DELETE FROM notes WHERE id = ?").run(Number(req.params.id));
    if (info.changes === 0) return res.status(404).json({ error: "Note not found" });
    res.json({ ok: true });
  }));
}
