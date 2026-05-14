import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAsync } from "../hooks";

export default function Home() {
  const nav = useNavigate();
  const stats = useAsync(() => api.stats(), []);

  return (
    <>
      <h1>Aegis — Integrated Content Management Tool</h1>
      <div className="subtitle">
        One tool over the Bulk Import workbook database. Build Assessments and
        Build Concepts; everything is written back in the canonical Bulk Import
        format, append-only.
      </div>

      <div className="grid cols-2" style={{ marginTop: 8 }}>
        <button className="module-card" onClick={() => nav("/build-assessments")}>
          <div className="module-title">1 · Build Assessments</div>
          <div className="module-desc">
            From Concept Mapping — drill the directory, stack Blueprint settings,
            generate. Or From Upload — PDF/text/image → MMD → deposit → generate.
          </div>
        </button>
        <button className="module-card" onClick={() => nav("/build-concepts")}>
          <div className="module-title">2 · Build Concepts</div>
          <div className="module-desc">
            Post Learning — upload a document, convert to MMD, parse concepts.
            Pre Learning — upload, or derive from existing Post Learning chapters.
          </div>
        </button>
      </div>

      {stats.data && (
        <>
          <div className="section-title">Database snapshot</div>
          <div className="grid cols-4">
            <Stat label="Chapters" value={stats.data.chapters} />
            <Stat label="Topics" value={stats.data.topics} />
            <Stat label="Concepts" value={stats.data.concepts} />
            <Stat label="Questions" value={stats.data.questions} />
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <span className={`badge ${stats.data.openai_live ? "green" : "yellow"}`}>
              OpenAI {stats.data.openai_live ? "live" : "dry-mode"}
            </span>
            <span className={`badge ${stats.data.mathpix_live ? "green" : "yellow"}`}>
              Mathpix {stats.data.mathpix_live ? "live" : "dry-mode"}
            </span>
            <span className="muted">
              Dry-mode produces realistic stub content so every flow runs without API keys.
            </span>
          </div>
        </>
      )}
      {stats.error && <div className="error-box">{stats.error}</div>}
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
