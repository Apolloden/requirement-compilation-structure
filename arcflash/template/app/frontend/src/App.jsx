import { Routes, Route } from "react-router-dom";
import { AppProvider, Layout } from "./shared.jsx";
import { PageHome } from "./pages/main.jsx";

export default function App() {
  return (
    <AppProvider>
      <Routes>
        <Route path="/" element={<Layout><PageHome /></Layout>} />
      </Routes>
    </AppProvider>
  );
}
