import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Pipeline from "./pages/Pipeline";
import Concepts from "./pages/Concepts";
import Questions from "./pages/Questions";
import Tagging from "./pages/Tagging";

const NAV = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/pipeline", label: "Pipeline" },
  { to: "/concepts", label: "Concepts" },
  { to: "/questions", label: "Questions" },
  { to: "/tagging", label: "Assessment Tagging" },
];

export default function App() {
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          Aegis
          <small>Content Intelligence Engine</small>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/concepts" element={<Concepts />} />
          <Route path="/questions" element={<Questions />} />
          <Route path="/tagging" element={<Tagging />} />
        </Routes>
      </main>
    </div>
  );
}
