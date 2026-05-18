import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import BuildAssessments from "./pages/BuildAssessments";
import BuildConcepts from "./pages/BuildConcepts";
import Database from "./pages/Database";
import ManualEntry from "./pages/ManualEntry";

const NAV = [
  { to: "/home", label: "Home" },
  { to: "/create", label: "Create" },
  { to: "/build-assessments", label: "Build Assessments" },
  { to: "/build-concepts", label: "Build Concepts" },
  { to: "/database", label: "Database" },
];

export default function App() {
  return (
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
          <Route path="/create" element={<ManualEntry />} />
          <Route path="/build-assessments" element={<BuildAssessments />} />
          <Route path="/build-concepts" element={<BuildConcepts />} />
          <Route path="/database" element={<Database />} />
        </Routes>
      </main>
    </div>
  );
}
