import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";
import DirectoryPicker from "../components/DirectoryPicker";
import type { Outcome, PreviewResult, Question, Scope, TagResult } from "../types";

const OUTCOME_CLASS: Record<Outcome, string> = {
  ADD: "green",
  TAG: "accent",
  SKIP: "yellow",
};

export default function Tagging() {
  const questions = useAsync(() => api.questions({ limit: "200" }), []);

  return (
    <>
      <h1>Tagging</h1>
      <div className="subtitle">
        An assessment or concept is one entity that can live in many places. Tag
        it under another concept/topic and it is written to the Bulk Import sheet
        as a repeated row with the <em>same</em> identity — the CMS reads that as
        a many-to-many tag, never a duplicate.
      </div>

      {questions.error && <div className="error-box">{questions.error}</div>}
      {questions.data && (
        <>
          <TagAssessment questions={questions.data} />
          <ImportPreview questions={questions.data} />
        </>
      )}
    </>
  );
}

/* --------------------------- tag an assessment --------------------------- */

function TagAssessment({ questions }: { questions: Question[] }) {
  const [questionId, setQuestionId] = useState<number | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TagResult | null>(null);

  const targetConcept = scope && scope.type === "concept" && scope.ids.length === 1
    ? scope.ids[0] : null;

  async function tag() {
    if (!questionId || !targetConcept) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.tagQuestionToConcept(questionId, targetConcept));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">Tag an assessment under another concept</div>
      <div className="card">
        <div className="field">
          <label className="field-label" htmlFor="tag-assessment">Assessment</label>
          <select
            id="tag-assessment"
            value={questionId ?? ""}
            onChange={(e) => setQuestionId(e.target.value ? Number(e.target.value) : null)}
            style={{ width: "100%" }}
          >
            <option value="">Select an assessment…</option>
            {questions.map((q) => (
              <option key={q.id} value={q.id}>
                [{q.sheet_kind}] {q.question_label} — {q.question.slice(0, 60)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <div className="field-label">Target concept (pick a single concept)</div>
          <DirectoryPicker onScope={setScope} />
        </div>
        <div className="row">
          <span className="muted">
            {targetConcept
              ? `Target: ${scope?.label}`
              : "Open a chapter and choose Specific concepts → exactly one"}
          </span>
          <div className="spacer" />
          <button disabled={!questionId || !targetConcept || busy} onClick={tag}>
            Tag assessment here
          </button>
        </div>
        {error && <div className="error-box mt-12">{error}</div>}
        {result && (
          <div className={`card mt-12 ${result.status === "tagged" ? "success-card" : ""}`}>
            {result.status === "tagged" ? (
              <span>
                <span className="badge green">tagged</span>{" "}
                <span className="mono">{result.question_label}</span> is now also under{" "}
                <strong>{result.concept_title}</strong>. On export it will be written
                as a repeated row (same label) — a tag, not a duplicate.
              </span>
            ) : (
              <span>
                <span className="badge">noop</span> {result.reason}
              </span>
            )}
          </div>
        )}
      </div>
    </>
  );
}

/* ------------------------------ preview ------------------------------ */

function ImportPreview({ questions }: { questions: Question[] }) {
  const [picked, setPicked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);

  function toggle(id: number) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.preview(picked, []));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">Import preview — what the CMS will do per row</div>
      <div className="card">
        <div className="muted mb-8">
          Select assessments and preview each row the export will emit against the
          current append-only output workbook:
          {" "}<span className="badge green">ADD</span> new ·{" "}
          <span className="badge accent">TAG</span> existing under a new placement ·{" "}
          <span className="badge yellow">SKIP</span> already present (CMS shows an error).
        </div>
        <div className="pick-list">
          {questions.slice(0, 60).map((q) => (
            <label key={q.id} className="pick-item">
              <input type="checkbox" checked={picked.includes(q.id)} onChange={() => toggle(q.id)} />
              <span className="mono">{q.question_label}</span>
              <span className="muted">{q.sheet_kind}</span>
            </label>
          ))}
        </div>
        <div className="row mt-12">
          <span className="muted">{picked.length} selected</span>
          <div className="spacer" />
          <button disabled={busy || picked.length === 0} onClick={run}>
            Preview outcome
          </button>
        </div>
        {error && <div className="error-box mt-12">{error}</div>}
      </div>

      {preview && (
        <div className="card mt-12">
          <div className="row">
            <span className="badge green">ADD {preview.summary.ADD ?? 0}</span>
            <span className="badge accent">TAG {preview.summary.TAG ?? 0}</span>
            <span className="badge yellow">SKIP {preview.summary.SKIP ?? 0}</span>
          </div>
          {preview.rows.length === 0 ? (
            <div className="empty">No rows to preview yet.</div>
          ) : (
            <div className="table-wrap mt-12">
              <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Outcome</th>
                    <th scope="col">Identity</th>
                    <th scope="col">Chapter</th>
                    <th scope="col">Topic</th>
                    <th scope="col">Concept</th>
                    <th scope="col">Group</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((r, i) => (
                    <tr key={i}>
                      <td><span className={`badge ${OUTCOME_CLASS[r.outcome]}`}>{r.outcome}</span></td>
                      <td className="mono">{r.identity}</td>
                      <td>{r.placement.chapter}</td>
                      <td>{r.placement.topic}</td>
                      <td>{r.placement.concept ?? "—"}</td>
                      <td>{r.placement.group_type ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
