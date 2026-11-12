import { createContext, useContext } from "react";
import { Link } from "react-router-dom";

const AppContext = createContext({});

export function AppProvider({ children }) {
  return <AppContext.Provider value={{}}>{children}</AppContext.Provider>;
}

export function useApp() {
  return useContext(AppContext);
}

export function Layout({ children }) {
  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="border-b border-gray-200 bg-white">
        <nav className="mx-auto flex max-w-3xl gap-4 px-4 py-3">
          <Link to="/notes" className="font-semibold text-blue-700 hover:underline">Notes</Link>
        </nav>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6">{children}</main>
    </div>
  );
}
