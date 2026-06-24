import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import DirectoryPicker from "../components/DirectoryPicker";
import DocumentUpload from "../components/DocumentUpload";
import type { BlueprintBatch, Scope, Session, UploadJob, Vocab } from "../types";

type Path = null | "concept_mapping" | "upload";

export default function BuildAssessments() {
  const [path, setPath] = useState<Path>(null);
  const vocab = useAsync(() => api.vocab(), []);

  return (
    <>
      <h1>Build Assessments</h1>
      <div className="subtitle">Create assessments from the concept-mapping database, or from an upload.</div>

      {!path && (
        <div className="grid cols-2">
          <button className="module-card" onClick={() => setPath("concept_mapping")}>
            <div className="module-title">a · From Concept Mapping</div>
            <div className="module-desc">
              Select Board → Class → Subject → Unit → Chapter. Scope to the whole
              chapter, specific topics, or specific concepts. Stack Blueprint
              settings, then generate.
            </div>
          </button>
          <button className="module-card" onClick={() => setPath("upload")}>
            <div className="module-title">b · From Upload</div>
            <div className="module-desc">
              Upload a PDF / text / handwritten image. Convert to MMD, choose the
              upload type, pick where to deposit, then identify & generate.
            </div>
          </button>
        </div>
      )}

      {path && (
        <button className="ghost" onClick={() => setPath(null)} style={{ marginBottom: 16 }}>
          ← Back to options
        </button>
      )}
      {path === "concept_mapping" && vocab.data && <ConceptMappingFlow vocab={vocab.data} />}
      {path === "upload" && vocab.data && <UploadFlow vocab={vocab.data} />}
    </>
  );
}

/* ----------------------------- multi-select ----------------------------- */

function MultiSelect({
  label, options, value, onChange,
}: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="field">
      <div className="field-label">{label}</div>
      <div className="chips">
        {options.map((o) => {
          const on = value.includes(o);
          return (
            <button
              key={o}
              type="button"
              className={`chip ${on ? "chip-on" : ""}`}
              onClick={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])}
            >
              {o}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* -------------------------- concept mapping flow ------------------------- */

function ConceptMappingFlow({ vocab }: { vocab: Vocab }) {
  const { run } = useRunConsole();
  const [scope, setScope] = useState<Scope | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  // Draft blueprint settings before "Save settings" (= Add batch).
  const [skills, setSkills] = useState<string[]>([]);
  const [difficulties, setDifficulties] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [appearsIn, setAppearsIn] = useState<string[]>([]);
  const [qType, setQType] = useState("objective");
  const [count, setCount] = useState(1);

  async function startSession() {
    if (!scope) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await api.createSession(scope.type, scope.ids));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const batch: Omit<BlueprintBatch, "id"> = {
        cognitive_skills: skills,
        difficulty_levels: difficulties,
        categories,
        question_type: qType,
        num_questions: count,
        appears_in: appearsIn,
      };
      await api.addBatch(session.id, batch);
      setSession(await api.getSession(session.id));
      setSkills([]); setDifficulties([]); setCategories([]); setAppearsIn([]); setCount(1);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const data = await run<Record<string, unknown>>(
        "Build Assessments — generating questions",
        api.paths.sessionGenerate(session.id));
      setResult(data);
      setSession(await api.getSession(session.id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">1 · Select scope from the directory</div>
      <div className="card">
        <DirectoryPicker onScope={setScope} />
        <div className="row" style={{ marginTop: 12 }}>
          <span className="muted">{scope ? `Scope: ${scope.type} — ${scope.label}` : "No scope selected"}</span>
          <div className="spacer" />
          <button disabled={!scope || busy || !!session} onClick={startSession}>
            Start session
          </button>
        </div>
      </div>

      {session && (
        <>
          <div className="section-title">2 · Blueprint settings (stack multiple before generating)</div>
          <div className="card">
            <MultiSelect label="Cognitive Skill" options={vocab.cognitive_skills}
              value={skills} onChange={setSkills} />
            <MultiSelect label="Difficulty Level" options={vocab.difficulty_levels}
              value={difficulties} onChange={setDifficulties} />
            <div className="field">
              <div className="field-label">Question Type</div>
              <div className="chips">
                {vocab.question_types.map((t) => (
                  <button key={t} type="button" className={`chip ${qType === t ? "chip-on" : ""}`}
                    onClick={() => { setQType(t); setCategories([]); }}>
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <MultiSelect label="Category Level" options={vocab.question_categories[qType] ?? []}
              value={categories} onChange={setCategories} />
            <MultiSelect label="Appears In (assessment purpose)" options={vocab.appears_in}
              value={appearsIn} onChange={setAppearsIn} />
            <div className="row" style={{ marginTop: 8 }}>
              <div className="field-label" style={{ margin: 0 }}>No. of questions per sub-category</div>
              <input type="number" min={1} max={20} value={count}
                onChange={(e) => setCount(Math.max(1, Number(e.target.value)))} style={{ width: 80 }} />
              <div className="spacer" />
              <button className="ghost" disabled={busy} onClick={saveSettings}>Save settings</button>
            </div>
          </div>

          {session.batches.length > 0 && (
            <div className="card" style={{ marginTop: 12 }}>
              <strong>Saved blueprint batches</strong>
              <table style={{ marginTop: 8 }}>
                <thead>
                  <tr><th>#</th><th>Type</th><th>Skills</th><th>Difficulty</th><th>Categories</th><th>Qs each</th></tr>
                </thead>
                <tbody>
                  {session.batches.map((b, i) => (
                    <tr key={b.id}>
                      <td>{i + 1}</td>
                      <td><span className="badge accent">{b.question_type}</span></td>
                      <td>{b.cognitive_skills.join(", ")}</td>
                      <td>{b.difficulty_levels.join(", ")}</td>
                      <td>{b.categories.join(", ")}</td>
                      <td>{b.num_questions}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row" style={{ marginTop: 12 }}>
                <div className="spacer" />
                <button disabled={busy} onClick={generate}>Generate questions</button>
              </div>
            </div>
          )}
        </>
      )}

      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}
      {result && <ResultCard result={result} />}
    </>
  );
}

/* ------------------------------ upload flow ------------------------------ */

function UploadFlow({ vocab }: { vocab: Vocab }) {
  const { run } = useRunConsole();
  const [uploadType, setUploadType] = useState("textbook");
  const [job, setJob] = useState<UploadJob | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [qType, setQType] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  async function chooseTextbookMode(mode: string) {
    if (!job) return;
    setBusy(true);
    try {
      setJob(await api.setTextbookMode(job.id, mode));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function deposit() {
    if (!job || !scope) return;
    setBusy(true);
    setError(null);
    try {
      setJob(await api.setDeposit(job.id, scope.type, scope.ids));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!job) return;
    setBusy(true);
    setError(null);
    try {
      const data = await run<Record<string, unknown>>(
        "Build Assessments — generating from upload",
        api.paths.assessmentGenerate(job.id),
        { body: JSON.stringify({ question_type: qType }) },
      );
      setResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  // Steps below only appear once the document has actually been converted to MMD.
  const converted = !!job && job.status !== "uploaded";
  const needsTextbookMode = converted && job!.upload_type === "textbook" && !job!.textbook_mode;

  return (
    <>
      <div className="section-title">1 · Upload type & file</div>
      <div className="field">
        <div className="field-label">Type of upload</div>
        <div className="chips">
          {vocab.upload_types.map((t) => (
            <button key={t} type="button" className={`chip ${uploadType === t ? "chip-on" : ""}`}
              disabled={!!job} onClick={() => setUploadType(t)}>
              {t.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </div>
      <DocumentUpload module="assessments" uploadType={uploadType}
        bookSources={vocab.book_sources} onJob={setJob} />

      {needsTextbookMode && (
        <>
          <div className="section-title">2 · Textbook — extract or create?</div>
          <div className="card row">
            <button className="ghost" disabled={busy} onClick={() => chooseTextbookMode("extract")}>
              Extract existing questions & answers
            </button>
            <button className="ghost" disabled={busy} onClick={() => chooseTextbookMode("create")}>
              Create my own questions
            </button>
          </div>
        </>
      )}

      {converted && !needsTextbookMode && (
        <>
          <div className="section-title">{job!.upload_type === "textbook" ? "3" : "2"} · Where to deposit</div>
          <div className="card">
            <DirectoryPicker onScope={setScope} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `${scope.type} — ${scope.label}` : "Select board → subject → chapter (mandatory)"}</span>
              <div className="spacer" />
              <button disabled={!scope || busy || job!.status === "deposited" || job!.status === "generated"}
                onClick={deposit}>
                Set deposit target
              </button>
            </div>
          </div>
        </>
      )}

      {job?.status === "deposited" && (
        <>
          <div className="section-title">Generate</div>
          <div className="card">
            <div className="muted" style={{ marginBottom: 8 }}>
              Aegis absorbs whatever the document contains. Leave this on{" "}
              <strong>Auto</strong> to detect and extract a mix of objective,
              subjective and descriptive questions (sub-questions included), or
              force a single type.
            </div>
            <div className="row">
              <div className="field-label" style={{ margin: 0 }}>Question type</div>
              <select value={qType} onChange={(e) => setQType(e.target.value)}>
                <option value="auto">Auto — detect all types</option>
                {vocab.question_types.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <div className="spacer" />
              <button disabled={busy} onClick={generate}>Identify & generate questions</button>
            </div>
          </div>
        </>
      )}

      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}
      {result && <ResultCard result={result} />}
    </>
  );
}

/* ------------------------------- result ------------------------------- */

function ResultCard({ result }: { result: Record<string, unknown> }) {
  const ids = (result.question_ids as number[] | undefined) ?? [];
  return (
    <div className="card success-card" style={{ marginTop: 16 }}>
      <strong>Generated · post-generation pipeline complete</strong>
      <pre className="mono" style={{ marginTop: 8 }}>{JSON.stringify(result, null, 2)}</pre>
      <div className="row" style={{ marginTop: 12 }}>
        {ids.length > 0 && (
          <a href={api.exportQuestionsUrl(ids)}>
            <button>⬇ Download Excel (Bulk Import)</button>
          </a>
        )}
        <span className="muted">
          {ids.length > 0
            ? `${ids.length} question(s) in the canonical Bulk Import format.`
            : "Rows were appended to the Bulk Import output workbook — download it from the Database tab."}
        </span>
      </div>
    </div>
  );
}
