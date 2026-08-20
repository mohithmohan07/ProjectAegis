import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import BuildAssessments from "./pages/BuildAssessments";
import BuildConcepts from "./pages/BuildConcepts";
import ReleaseReview from "./pages/ReleaseReview";
import Tagging from "./pages/Tagging";
import Workbooks from "./pages/Workbooks";
import Database from "./pages/Database";
import Admin from "./pages/Admin";
import { RunConsoleProvider } from "./RunConsole";
import RunConsolePanel from "./components/RunConsolePanel";
import { AuthAccount, AuthGate, AuthProvider } from "./Auth";
import Logo from "./components/Logo";
import { THEME_LABEL, useTheme } from "./theme";

const NAV = [
  { to: "/home", label: "Home" },
  { to: "/build-assessments", label: "Build Assessments" },
  { to: "/build-concepts", label: "Build Concepts" },
  { to: "/tagging", label: "Tagging" },
  { to: "/workbooks", label: "Create Workbooks" },
  { to: "/database", label: "Database" },
  { to: "/admin", label: "Admin" },
];

function ThemeToggle() {
  const { choice, cycle } = useTheme();
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={cycle}
      aria-label="Switch theme"
      title="Switch between auto, light and dark themes"
    >
      <span aria-hidden="true">◐</span>
      <span className="theme-toggle-label">{THEME_LABEL[choice]} theme</span>
    </button>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AuthGate>
        <RunConsoleProvider>
          <div className="layout">
            <aside className="sidebar">
              <div className="brand">
                <Logo />
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
              <div className="sidebar-note">
                The Bulk Import workbook is the single source of truth. Every
                generation is written back to it — append-only.
              </div>
              <div className="spacer" />
              <ThemeToggle />
              <AuthAccount />
            </aside>
            <main className="main">
              <Routes>
                <Route path="/" element={<Navigate to="/home" replace />} />
                <Route path="/home" element={<Home />} />
                <Route path="/build-assessments" element={<BuildAssessments />} />
                <Route path="/build-concepts" element={<BuildConcepts />} />
                {/* Deep-linked review surface — deliberately not a NAV tab. */}
                <Route path="/build-concepts/review/:jobId" element={<ReleaseReview />} />
                <Route path="/tagging" element={<Tagging />} />
                <Route path="/workbooks" element={<Workbooks />} />
                <Route path="/database" element={<Database />} />
                <Route path="/admin" element={<Admin />} />
              </Routes>
            </main>
            <RunConsolePanel />
          </div>
        </RunConsoleProvider>
      </AuthGate>
    </AuthProvider>
  );
}
