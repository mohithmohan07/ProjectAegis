import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";

const SHEETS = ["objective", "subjective", "descriptive"];

export default function Database() {
  const [sheet, setSheet] = useState("objective");
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const stats = useAsync(() => api.stats(), []);
  const questions = useAsync(() => api.questions({ sheet_kind: sheet, limit: "100" }), [sheet]);

  async function importWorkbook(file: File) {
    setBusy(true);
    setImportMsg(null);
    try {
      const counts = await api.importWorkbook(file);
      setImportMsg(`Imported: ${JSON.stringify(counts)}`);
      stats.reload();
      questions.reload();
    } catch (e) {
      setImportMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Database</h1>
      <div className="subtitle">
        The Bulk Import workbook is the source of truth. Import a workbook to load
        it, or export the canonical 3-sheet format.
      </div>

      <div className="card">
        <div className="row">
          <a href={api.exportUrl("all")}>
            <button className="ghost">Export full database (.xlsx)</button>
          </a>
          <a href={api.exportUrl("output")}>
            <button className="ghost">Export append-only output (.xlsx)</button>
          </a>
          <div className="spacer" />
          <label className="upload-label">
            Import Bulk Import workbook
            <input type="file" accept=".xlsx" disabled={busy} style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && importWorkbook(e.target.files[0])} />
          </label>
        </div>
        {importMsg && <div className="muted mono" style={{ marginTop: 8 }}>{importMsg}</div>}
      </div>

      {stats.data && (
        <div className="grid cols-4" style={{ marginTop: 16 }}>
          <Stat label="Chapters" value={stats.data.chapters} />
          <Stat label="Topics" value={stats.data.topics} />
          <Stat label="Concepts" value={stats.data.concepts} />
          <Stat label="Groups" value={stats.data.groups} />
        </div>
      )}

      <div className="section-title">Questions ({sheet})</div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row">
          {SHEETS.map((s) => (
            <button key={s} className={sheet === s ? "" : "ghost"} onClick={() => setSheet(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>

      {questions.error && <div className="error-box">{questions.error}</div>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Category</th>
              <th>Cognitive</th>
              <th>Difficulty</th>
              <th>Marks</th>
              <th>Question</th>
              <th>Origin</th>
            </tr>
          </thead>
          <tbody>
            {questions.data?.map((q) => (
              <tr key={q.id}>
                <td className="mono">{q.question_label}</td>
                <td>{q.question_category}</td>
                <td><span className="badge">{q.cognitive_skills}</span></td>
                <td>{q.level_of_difficulty}</td>
                <td>{q.marks}</td>
                <td>{q.question.slice(0, 140)}</td>
                <td><span className="badge accent">{q.origin}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
        {questions.data?.length === 0 && <div className="empty">No questions in this sheet yet.</div>}
      </div>
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
