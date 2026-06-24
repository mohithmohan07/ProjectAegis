import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import BuildAssessments from "./pages/BuildAssessments";
import BuildConcepts from "./pages/BuildConcepts";
import Tagging from "./pages/Tagging";
import Workbooks from "./pages/Workbooks";
import Database from "./pages/Database";
import Admin from "./pages/Admin";
import { RunConsoleProvider } from "./RunConsole";
import RunConsolePanel from "./components/RunConsolePanel";

const NAV = [
  { to: "/home", label: "Home" },
  { to: "/build-assessments", label: "Build Assessments" },
  { to: "/build-concepts", label: "Build Concepts" },
  { to: "/tagging", label: "Tagging" },
  { to: "/workbooks", label: "Create Workbooks" },
  { to: "/database", label: "Database" },
  { to: "/admin", label: "Admin" },
];

export default function App() {
  return (
    <RunConsoleProvider>
      <div className="layout">
        <aside className="sidebar">
          <div className="brand">
            Aegis
            <small>Integrated Content Tool</small>
          </div>
          <nav className="nav">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-note">
            The Bulk Import workbook is the single source of truth. Every
            generation is written back to it — append-only.
          </div>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Navigate to="/home" replace />} />
            <Route path="/home" element={<Home />} />
            <Route path="/build-assessments" element={<BuildAssessments />} />
            <Route path="/build-concepts" element={<BuildConcepts />} />
            <Route path="/tagging" element={<Tagging />} />
            <Route path="/workbooks" element={<Workbooks />} />
            <Route path="/database" element={<Database />} />
            <Route path="/admin" element={<Admin />} />
          </Routes>
        </main>
        <RunConsolePanel />
      </div>
    </RunConsoleProvider>
  );
}
