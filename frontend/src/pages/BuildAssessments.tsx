import { useState } from "react";
import { api } from "../api/client";
import type {
  AssessmentRelease,
  AssessmentReleaseIssues,
  AssessmentReleaseUploadResult,
} from "../api/client";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import DirectoryPicker from "../components/DirectoryPicker";
import DocumentUpload from "../components/DocumentUpload";
import ApiUsageSummary from "../components/ApiUsageSummary";
import type { BlueprintBatch, OpenAIUsage, Scope, Session, UploadJob, Vocab } from "../types";

type Path = null | "concept_mapping" | "upload" | "release";

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
          <button className="module-card" onClick={() => setPath("release")}>
            <div className="module-title">c · Assessment Release review</div>
            <div className="module-desc">
              Review a generated assessment release: readiness and issues, the Concept
              File and Master File downloads, and the explicit Upload Master to
              Database action. Nothing publishes to the database automatically.
            </div>
          </button>
        </div>
      )}

      {path && (
        <button className="ghost mb-16" onClick={() => setPath(null)}>
          ← Back to options
        </button>
      )}
      {path === "concept_mapping" && vocab.data && <ConceptMappingFlow vocab={vocab.data} />}
      {path === "upload" && vocab.data && <UploadFlow vocab={vocab.data} />}
      {path === "release" && <ReleaseReviewFlow />}
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
        <div className="row mt-12">
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
            <div className="field">
              <label className="field-label" htmlFor="cm-question-count">
                No. of questions per sub-category
              </label>
              <input id="cm-question-count" type="number" min={1} max={20} value={count}
                onChange={(e) => setCount(Math.max(1, Number(e.target.value)))} className="input-sm" />
            </div>
            <div className="row">
              <div className="spacer" />
              <button className="ghost" disabled={busy} onClick={saveSettings}>Save settings</button>
            </div>
          </div>

          <div className="section-title">3 · Saved blueprint batches</div>
          <div className="card">
            {session.batches.length === 0 ? (
              <div className="empty">
                No blueprint batches saved yet. Save settings above to stack the first batch.
              </div>
            ) : (
              <>
                <div className="table-wrap">
                  <table>
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
                </div>
                <div className="row mt-12">
                  <div className="spacer" />
                  <button className="primary" disabled={busy} onClick={generate}>Generate questions</button>
                </div>
              </>
            )}
          </div>
        </>
      )}

      {error && <div className="error-box mt-16">{error}</div>}
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
        {
          cumulative: true,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
      );
      setResult(data);
      try {
        setJob(await api.getUploadJob("assessments", job.id));
      } catch {
        // The cumulative result remains usable if the job refresh fails.
      }
    } catch (e) {
      setError(String(e));
      try {
        setJob(await api.getUploadJob("assessments", job.id));
      } catch {
        // Keep the generation error visible if refreshing usage also fails.
      }
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
      <div className="card mb-12">
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
        bookSources={vocab.book_sources} disabled={busy} onJob={setJob} />
      {!result && (
        <ApiUsageSummary
          usage={job?.openai_usage}
          filename={job?.filename}
          fileLabel="Source file"
          cumulative
        />
      )}

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
            <div className="row mt-12">
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
            <div className="muted mb-8">
              Aegis absorbs whatever the document contains. Leave this on{" "}
              <strong>Auto</strong> to detect and extract a mix of objective,
              subjective and descriptive questions (sub-questions included), or
              force a single type.
            </div>
            <div className="field">
              <label className="field-label" htmlFor="upload-question-type">Question type</label>
              <select id="upload-question-type" value={qType} onChange={(e) => setQType(e.target.value)}>
                <option value="auto">Auto — detect all types</option>
                {vocab.question_types.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className="row">
              <div className="spacer" />
              <button className="primary" disabled={busy} onClick={generate}>Identify & generate questions</button>
            </div>
          </div>
        </>
      )}

      {error && <div className="error-box mt-16">{error}</div>}
      {result && <ResultCard result={result} filename={job?.filename} />}
    </>
  );
}

/* ------------------------------- result ------------------------------- */

function ResultCard({
  result,
  filename,
}: {
  result: Record<string, unknown>;
  filename?: string;
}) {
  const ids = (result.question_ids as number[] | undefined) ?? [];
  const usage = result.openai_usage as OpenAIUsage | undefined;
  const status = typeof result.status === "string" ? result.status : "complete";
  // Mechanical display of whatever counts the result reports: numbers as-is,
  // arrays by length. No interpretation — the raw JSON stays available below.
  const counts = Object.entries(result).flatMap(([key, value]): Array<[string, number]> => {
    if (typeof value === "number") return [[key, value]];
    if (Array.isArray(value)) return [[key, value.length]];
    return [];
  });
  return (
    <div className="card success-card mt-16">
      <div className="row">
        <strong>Generated · post-generation pipeline complete</strong>
        <span className={`badge ${/error|fail/i.test(status) ? "red" : "green"}`}>{status}</span>
      </div>
      {counts.length > 0 && (
        <div className="row mt-8">
          {counts.map(([key, n]) => (
            <span key={key} className="muted">
              {key.replace(/_/g, " ")}: <strong>{n}</strong>
            </span>
          ))}
        </div>
      )}
      <ApiUsageSummary
        usage={usage}
        filename={filename}
        fileLabel="Source file"
        cumulative={Boolean(filename)}
      />
      <details className="mt-12">
        <summary>Raw result JSON</summary>
        <pre className="mono mt-8">{JSON.stringify(result, null, 2)}</pre>
      </details>
      <div className="row mt-12">
        {ids.length > 0 && (
          <a className="button-link" href={api.exportQuestionsUrl(ids)}>
            ⬇ Download Excel (Bulk Import)
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

/* ----------------------- Assessment release review ----------------------- */

function ReleaseReviewFlow() {
  const [releaseId, setReleaseId] = useState("");
  const [release, setRelease] = useState<AssessmentRelease | null>(null);
  const [issues, setIssues] = useState<AssessmentReleaseIssues | null>(null);
  const [uploadResult, setUploadResult] =
    useState<AssessmentReleaseUploadResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setError(""); setUploadResult(null); setRelease(null); setIssues(null);
    const id = Number(releaseId);
    if (!id) { setError("Enter a release id."); return; }
    try {
      setRelease(await api.getAssessmentRelease(id));
      setIssues(await api.getAssessmentReleaseIssues(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const upload = async () => {
    if (!release) return;
    setBusy(true); setError("");
    try {
      setUploadResult(await api.uploadReleaseMaster(release.id));
      setRelease(await api.getAssessmentRelease(release.id));
    } catch (e) {
      // A refusal is a refusal: show the exact reason, never a fake success.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const blocked = release?.readiness === "blocked_for_database_upload";
  const unplaced = issues?.issues?.unplaced ?? [];

  return (
    <>
      <div className="section-title">Assessment Release</div>
      <div className="card">
        <div className="row">
          <input
            aria-label="Release id"
            placeholder="Release id"
            value={releaseId}
            onChange={(e) => setReleaseId(e.target.value)}
            className="input-sm"
          />
          <button onClick={load}>Load</button>
        </div>
        {error && <div className="error mt-8">{error}</div>}
        {release && (
          <div className="mt-12">
            <div>
              <strong>{release.release_uid}</strong> v{release.version} — state{" "}
              <code>{release.state}</code>, readiness{" "}
              <code>{release.readiness || "unpublished"}</code>
            </div>
            {release.published ? (
              <div className="row mt-12">
                <a className="button-link ghost" href={api.releaseConceptsUrl(release.id)}>
                  Download Concept File
                </a>
                <a className="button-link ghost" href={api.releaseMasterUrl(release.id)}>
                  Download Master File
                </a>
              </div>
            ) : (
              <div className="muted mt-8">
                This release has no published artifacts yet.
              </div>
            )}
            {(issues?.payload_errors?.length ?? 0) > 0 && (
              <div className="error mt-8">
                {issues!.payload_errors.map((e, i) => <div key={i}>{e}</div>)}
              </div>
            )}
            {unplaced.length > 0 && (
              <div className="error mt-8">
                {unplaced.map((u, i) => (
                  <div key={i}>
                    Unplaced: {u.question_label || u.candidate_id} — {u.reason}
                  </div>
                ))}
              </div>
            )}
            <div className="mt-12">
              <button
                className="primary"
                disabled={busy || blocked || !release.published || release.uploaded}
                onClick={upload}
                title={blocked
                  ? "Blocked for database upload — resolve the named issues and publish a new version"
                  : undefined}
              >
                {busy && <><span className="spinner" aria-hidden="true" />{" "}</>}
                {release.uploaded
                  ? "Already uploaded"
                  : blocked
                    ? "Blocked for database upload"
                    : "Upload Master to Database"}
              </button>
            </div>
            {uploadResult && (
              <div className="mt-8">
                Uploaded {uploadResult.release_uid} v{uploadResult.version}:{" "}
                {uploadResult.questions_created} question(s),{" "}
                {uploadResult.groups_created} group(s) created.
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
