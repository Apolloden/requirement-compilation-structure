import { Routes, Route } from "react-router-dom";
import { AppProvider, Layout } from "./shared.jsx";
import { PageHome, PageNotes, PageNoteNew, PageNote } from "./pages/m1.jsx";

export default function App() {
  return (
    <AppProvider>
      <Routes>
        <Route path="/" element={<Layout><PageHome /></Layout>} />
        <Route path="/notes" element={<Layout><PageNotes /></Layout>} />
        <Route path="/notes/new" element={<Layout><PageNoteNew /></Layout>} />
        <Route path="/notes/:id" element={<Layout><PageNote /></Layout>} />
        <Route path="*" element={<Layout><h1 className="text-2xl font-bold">Page not found</h1></Layout>} />
      </Routes>
    </AppProvider>
  );
}
