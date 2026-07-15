import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import DirectoryPicker from "../components/DirectoryPicker";
import DocumentUpload from "../components/DocumentUpload";
import SyllabusUploader from "../components/SyllabusUploader";
import type { Scope, UploadJob } from "../types";

type Path = null | "post" | "pre";

export default function BuildConcepts() {
  const [path, setPath] = useState<Path>(null);
  const vocab = useAsync(() => api.vocab(), []);
  const bookSources = vocab.data?.book_sources ?? [];

  return (
    <>
      <h1>Build Concepts</h1>
      <div className="subtitle">
        Generate concepts from documents (Post Learning) or derive prerequisite
        concepts (Pre Learning). Output is written to the Bulk Import workbook.
      </div>

      {!path && (
        <div className="grid cols-2">
          <button className="module-card" onClick={() => setPath("post")}>
            <div className="module-title">1 · Post Learning</div>
            <div className="module-desc">
              Upload a document → convert to MMD → parse concepts → deposit under a chapter.
            </div>
          </button>
          <button className="module-card" onClick={() => setPath("pre")}>
            <div className="module-title">2 · Pre Learning</div>
            <div className="module-desc">
              Upload a document, or derive pre-learning concepts from one or more
              existing Post Learning chapters.
            </div>
          </button>
        </div>
      )}

      {path && (
        <button className="ghost" onClick={() => setPath(null)} style={{ marginBottom: 16 }}>
          ← Back to options
        </button>
      )}
      {path === "post" && <PostLearningFlow bookSources={bookSources} />}
      {path === "pre" && <PreLearningFlow bookSources={bookSources} />}
    </>
  );
}

/* ----------------------------- post learning ----------------------------- */

function PostLearningFlow({ bookSources }: { bookSources: string[] }) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [treeReload, setTreeReload] = useState(0);
  const canResume = Boolean(
    error && /type embedding failed|unassigned mined types/i.test(error),
  );

  async function generate() {
    if (!job || !scope) return;
    setBusy(true);
    setError(null);
    try {
      const data = await run<Record<string, unknown>>(
        "Post Learning — generating concepts",
        api.paths.postLearningGenerate(job.id),
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
      );
      setResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">1 · Upload document</div>
      <DocumentUpload module="concepts" conceptKind="post" bookSources={bookSources} onJob={setJob} />

      {job?.status === "converted" && (
        <>
          <div className="section-title">2 · Deposit concepts under a chapter</div>
          <div className="card">
            <DirectoryPicker onScope={setScope} chapterOnly reloadSignal={treeReload} />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `Chapter: ${scope.label}` : "Pick a chapter"}</span>
              <div className="spacer" />
              <button disabled={!scope || busy} onClick={generate}>
                {canResume ? "Resume failed Type assignment" : "Parse & generate concepts"}
              </button>
            </div>
            {canResume && (
              <div className="muted" style={{ marginTop: 8 }}>
                Earlier GPT stages are saved. Retrying resumes at Type assignment
                instead of regenerating from the beginning.
              </div>
            )}
          </div>
        </>
      )}

      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}
      {result && <ConceptResult result={result} />}
    </>
  );
}

/* ----------------------------- pre learning ----------------------------- */

function PreLearningFlow({ bookSources }: { bookSources: string[] }) {
  const [mode, setMode] = useState<"upload" | "existing">("upload");

  return (
    <>
      <div className="card row" style={{ marginBottom: 16 }}>
        <strong>Pre Learning source:</strong>
        <label className="radio">
          <input type="radio" checked={mode === "upload"} onChange={() => setMode("upload")} />
          Upload a document
        </label>
        <label className="radio">
          <input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} />
          Use existing Post Learning
        </label>
      </div>
      {mode === "upload"
        ? <PreLearningUpload bookSources={bookSources} />
        : <PreLearningExisting bookSources={bookSources} />}
    </>
  );
}

function PreLearningUpload({ bookSources }: { bookSources: string[] }) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(null);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [treeReload, setTreeReload] = useState(0);

  async function generate() {
    if (!job || !scope) return;
    setBusy(true);
    setError(null);
    try {
      const data = await run<Record<string, unknown>>(
        "Pre Learning — generating concepts",
        api.paths.preLearningGenerate(job.id),
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
      );
      setResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">1 · Upload document</div>
      <DocumentUpload module="concepts" conceptKind="pre" bookSources={bookSources} onJob={setJob} />
      {job?.status === "converted" && (
        <>
          <div className="section-title">2 · Deposit pre-learning concepts under a chapter</div>
          <div className="card">
            <DirectoryPicker onScope={setScope} chapterOnly reloadSignal={treeReload} />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `Chapter: ${scope.label}` : "Pick a chapter"}</span>
              <div className="spacer" />
              <button disabled={!scope || busy} onClick={generate}>Generate pre-learning concepts</button>
            </div>
          </div>
        </>
      )}
      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}
      {result && <ConceptResult result={result} />}
    </>
  );
}

function PreLearningExisting({ bookSources }: { bookSources: string[] }) {
  const { run } = useRunConsole();
  const [scope, setScope] = useState<Scope | null>(null);
  const [chapterIds, setChapterIds] = useState<number[]>([]);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  function addChapter() {
    if (scope && scope.type === "chapter" && !chapterIds.includes(scope.ids[0])) {
      setChapterIds([...chapterIds, scope.ids[0]]);
    }
  }

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      const data = await run<Record<string, unknown>>(
        "Pre Learning — deriving from existing chapters",
        api.paths.preLearningFromExisting,
        { body: JSON.stringify({ chapter_ids: chapterIds, source_book: source }) },
      );
      setResult(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">Choose Post Learning chapters (one or more)</div>
      <div className="card">
        <input placeholder="Source book (optional)" value={source}
          onChange={(e) => setSource(e.target.value)} list="book-sources" />
        <datalist id="book-sources">{bookSources.map((b) => <option key={b} value={b} />)}</datalist>
        <DirectoryPicker onScope={setScope} chapterOnly />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="ghost" disabled={!scope} onClick={addChapter}>
            + Add chapter {scope ? `(${scope.label})` : ""}
          </button>
          <div className="spacer" />
          <button disabled={busy || chapterIds.length === 0} onClick={generate}>
            Generate pre-learning concepts
          </button>
        </div>
        {chapterIds.length > 0 && (
          <div className="muted" style={{ marginTop: 8 }}>
            Selected chapter ids: {chapterIds.join(", ")}
          </div>
        )}
      </div>
      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}
      {result && <ConceptResult result={result} />}
    </>
  );
}

function ConceptResult({ result }: { result: Record<string, unknown> }) {
  const ids = (result.concept_ids as number[] | undefined) ?? [];
  const jobId = result.job_id as number | undefined;
  const inventoryItems = (result.inventory_items as number | undefined) ?? 0;
  return (
    <div className="card success-card" style={{ marginTop: 16 }}>
      <strong>Concepts written to the Bulk Import workbook (append-only)</strong>
      <pre className="mono" style={{ marginTop: 8 }}>{JSON.stringify(result, null, 2)}</pre>
      <div className="row" style={{ marginTop: 12 }}>
        {ids.length > 0 && (
          <a href={api.exportConceptsUrl(ids)}>
            <button>⬇ Download Excel (Bulk Import)</button>
          </a>
        )}
        {jobId != null && inventoryItems > 0 && (
          <a href={api.inventoryCsvUrl(jobId)}>
            <button className="ghost">⬇ Question/Task Inventory (CSV)</button>
          </a>
        )}
        <span className="muted">
          {ids.length > 0
            ? `${ids.length} concept(s) in the canonical Bulk Import format.` +
              (inventoryItems > 0
                ? ` ${inventoryItems} extracted question(s)/task(s) in the inventory CSV.`
                : "")
            : "Download the full output workbook from the Database tab."}
        </span>
      </div>
    </div>
  );
}
