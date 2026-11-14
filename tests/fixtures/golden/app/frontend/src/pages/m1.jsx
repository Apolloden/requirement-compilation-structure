import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";

export function PageHome() {
  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-bold">Notes</h1>
      <p>A small public notebook.</p>
    </section>
  );
}

export function PageNotes() {
  const [notes, setNotes] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get("/api/notes").then((r) => {
      if (r.ok) setNotes(r.data.notes);
      else setError(r.data.error || "Could not load the notes");
    });
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Notes</h1>
      <Link to="/notes/new" className="inline-block rounded bg-blue-700 px-3 py-1 text-white">Create Note</Link>
      {error && <div role="alert" className="text-red-700">{error}</div>}
      <section aria-label="Note list" className="rounded border border-gray-200 bg-white">
        <ul className="divide-y divide-gray-200">
          {notes.map((note) => (
            <li key={note.id} className="px-3 py-2">
              <Link to={`/notes/${note.id}`} className="text-blue-700 hover:underline">{note.title}</Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function PageNoteNew() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");

  async function save(event) {
    event.preventDefault();
    const r = await api.post("/api/notes", { title, body });
    if (r.ok) navigate(`/notes/${r.data.note.id}`);
    else setError(r.data.error || "Could not save the note");
  }

  return (
    <form noValidate onSubmit={save} className="space-y-3">
      <h1 className="text-2xl font-bold">Create Note</h1>
      {error && <div role="alert" className="text-red-700">{error}</div>}
      <div className="flex flex-col gap-1">
        <label htmlFor="note-title">Title</label>
        <input id="note-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} className="rounded border px-2 py-1" />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="note-body">Body</label>
        <textarea id="note-body" value={body} onChange={(e) => setBody(e.target.value)} className="rounded border px-2 py-1" />
      </div>
      <div className="flex gap-2">
        <button type="submit" className="rounded bg-blue-700 px-3 py-1 text-white">Save Note</button>
        <button type="button" onClick={() => navigate("/notes")} className="rounded border px-3 py-1">Cancel</button>
      </div>
    </form>
  );
}

export function PageNote() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [note, setNote] = useState(null);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    api.get(`/api/notes/${id}`).then((r) => {
      if (r.ok) setNote(r.data.note);
      else setError(r.data.error || "Could not load the note");
    });
  }, [id]);

  async function remove() {
    const r = await api.del(`/api/notes/${id}`);
    if (r.ok) navigate("/notes");
    else setError(r.data.error || "Could not delete the note");
  }

  if (error) return <div role="alert" className="text-red-700">{error}</div>;
  if (!note) return <p>Loading…</p>;

  return (
    <article className="space-y-3">
      <h1 className="text-2xl font-bold">{note.title}</h1>
      <p className="whitespace-pre-wrap">{note.body}</p>
      <button type="button" onClick={() => setConfirming(true)} className="rounded border border-red-700 px-3 py-1 text-red-700">Delete</button>
      {confirming && (
        <div role="dialog" aria-label="Delete note" className="space-y-2 rounded border border-gray-300 bg-white p-4">
          <p>Delete this note?</p>
          <div className="flex gap-2">
            <button type="button" onClick={remove} className="rounded bg-red-700 px-3 py-1 text-white">Confirm Delete</button>
            <button type="button" onClick={() => setConfirming(false)} className="rounded border px-3 py-1">Cancel</button>
          </div>
        </div>
      )}
    </article>
  );
}
