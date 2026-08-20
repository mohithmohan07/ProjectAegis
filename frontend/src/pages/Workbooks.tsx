import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import ApiUsageSummary, {
  formatEstimatedCost,
  formatTokenCount,
} from "../components/ApiUsageSummary";
import type { WorkbookResult } from "../types";

export default function Workbooks() {
  const { run } = useRunConsole();
  const meta = useAsync(() => api.workbookSubjects(), []);
  const library = useAsync(() => api.workbookLibrary(), []);
  const [subject, setSubject] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkbookResult | null>(null);

  async function generate() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("subject", subject);
      const data = await run<WorkbookResult>(
        `Create Workbooks — ${file.name}`, api.paths.workbookGenerate, { body: fd });
      setResult(data);
      library.reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Create Workbooks</h1>
      <div className="subtitle">
        Generate a student revision-workbook PDF from an NCERT chapter source PDF
        (PDF text → GPT → A4 render). Outputs publish subject-wise:
        Class NN → Subject → chapter.
        {meta.data && (
          <>
            {" "}
            <span className={`badge ${meta.data.live ? "green" : "yellow"}`}>
              {meta.data.live ? "live mode" : "keys missing — live required"}
            </span>
          </>
        )}
      </div>

      <div className="section-title">1 · Source chapter PDF + subject</div>
      <div className="card">
        <div className="row">
          <input
            type="file"
            accept=".pdf"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <select value={subject} onChange={(e) => setSubject(e.target.value)}>
            <option value="">Subject…</option>
            {meta.data?.subjects.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <div className="spacer" />
          <button disabled={!file || !subject || busy} onClick={generate}>
            {busy
              ? <><span className="spinner" aria-hidden="true" /> Generating…</>
              : "Generate workbook"}
          </button>
        </div>
        <div className="muted mt-8">
          Filename must follow the NCERT convention, e.g.{" "}
          <span className="mono">CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf</span> —
          grade, chapter number and title are read from it.
        </div>
      </div>

      {error && <div className="error-box mt-16">{error}</div>}

      {result && (
        <div className="card success-card mt-16">
          <div className="row">
            <strong>
              {result.meta.chapter_title} — {result.meta.subject} ·{" "}
              {result.meta.grade}
            </strong>
            <span className={`badge ${result.mode === "live" ? "green" : "accent"}`}>
              {result.mode}
            </span>
            <span className={`badge ${result.valid ? "green" : "yellow"}`}>
              {result.valid ? "valid" : "issues"}
            </span>
            <div className="spacer" />
            <a
              className="button-link ghost"
              href={api.workbookFileUrl(
                `${result.meta.grade_folder}/${result.meta.subject}/${result.meta.stem}.pdf`)}
            >
              Download PDF
            </a>
          </div>
          <ApiUsageSummary
            usage={result.openai_usage}
            filename={result.meta.stem ? `${result.meta.stem}.pdf` : undefined}
          />
          {result.log && <pre className="mmd-preview">{result.log}</pre>}
        </div>
      )}

      <div className="section-title">Workbook library (subject-wise)</div>
      <div className="card">
        {(library.data?.length ?? 0) === 0 && (
          <div className="empty">No workbooks generated yet.</div>
        )}
        {(library.data?.length ?? 0) > 0 && (
          <div className="table-wrap">
            <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Class</th>
                  <th scope="col">Subject</th>
                  <th scope="col">Workbook</th>
                  <th scope="col">Size</th>
                  <th scope="col">API tokens</th>
                  <th scope="col">Est. OpenAI cost</th>
                </tr>
              </thead>
              <tbody>
                {library.data!.map((e) => (
                  <tr key={e.rel}>
                    <td>{e.class_folder}</td>
                    <td>{e.subject}</td>
                    <td className="mono">
                      {/* The filename IS the download: the row's one
                          action lives in the first visible text cell, so
                          a phone user never swipes 7 columns to reach a
                          trailing PDF link. */}
                      <a href={api.workbookFileUrl(e.rel)}>{e.name}</a>
                    </td>
                    <td>{(e.size / 1024).toFixed(0)} KB</td>
                    <td className="mono">
                      {e.openai_usage ? formatTokenCount(e.openai_usage.total_tokens) : "—"}
                      {e.openai_usage && (
                        <div className="hint">
                          {formatTokenCount(e.openai_usage.input_tokens)} in
                          {" · "}
                          {formatTokenCount(e.openai_usage.output_tokens)} out
                        </div>
                      )}
                    </td>
                    <td className="mono">
                      {e.openai_usage
                        ? formatEstimatedCost(e.openai_usage.estimated_cost_usd)
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
