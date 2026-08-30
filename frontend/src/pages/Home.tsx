import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAsync } from "../hooks";

export default function Home() {
  const nav = useNavigate();
  const stats = useAsync(() => api.stats(), []);

  return (
    <>
      <h1>Aegis — Content Intelligence Engine</h1>
      <div className="subtitle">
        One tool over the Bulk Import workbook database. Build Assessments and
        Build Concepts; everything is written back in the canonical Bulk Import
        format, append-only.
      </div>

      <div className="grid cols-2 mt-8">
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
            Post Learning — upload a document, parse it, extract concepts,
            deposit under a chapter.
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
          <div className="row mt-12">
            <span className={`badge ${stats.data.openai_live ? "green" : "yellow"}`}>
              OpenAI {stats.data.openai_live ? "live" : "keys missing"}
            </span>
            {stats.data.openai_live ? (
              <span className="muted">All generation runs live — no dry stubs.</span>
            ) : (
              <span className="muted">
                Set the API key to enable live generation. Dry mode is disabled.
              </span>
            )}
          </div>
        </>
      )}
      {stats.error && <div className="error-box mt-16">{stats.error}</div>}
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
