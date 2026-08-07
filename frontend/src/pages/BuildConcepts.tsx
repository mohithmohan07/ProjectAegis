import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useOptionalAuth } from "../Auth";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import DirectoryPicker from "../components/DirectoryPicker";
import DocumentUpload from "../components/DocumentUpload";
import SyllabusUploader from "../components/SyllabusUploader";
import { ConceptReviewPanel } from "../components/ConceptReviewPanel";
import ApiUsageSummary from "../components/ApiUsageSummary";
import type {
  OpenAIUsage,
  PendingSemanticDecision,
  ResumableCheckpoint,
  Scope,
  UploadJob,
} from "../types";

type Path = null | "post" | "pre";

export default function BuildConcepts() {
  const auth = useOptionalAuth();
  const [path, setPath] = useState<Path>(null);
  const [resumeJob, setResumeJob] = useState<UploadJob | null>(null);
  const [pendingResume, setPendingResume] =
    useState<ResumableCheckpoint | null>(null);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resumeDiscoveryLoading, setResumeDiscoveryLoading] = useState(true);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const discoveredForUserRef = useRef("");
  const vocab = useAsync(() => api.vocab(), []);
  const bookSources = vocab.data?.book_sources ?? [];
  const ownerKey = auth?.user?.sub || "local";

  useEffect(() => {
    if (auth?.loading || discoveredForUserRef.current === ownerKey) return;
    discoveredForUserRef.current = ownerKey;
    let active = true;
    setResumeDiscoveryLoading(true);
    setResumeError(null);
    Promise.all([
      api.resumableConceptCheckpoints("post"),
      api.resumableConceptCheckpoints("pre"),
    ])
      .then(([post, pre]) => {
        if (!active) return;
        const candidate = [...post.items, ...pre.items]
          .filter((job) => job.checkpoint_available)
          .sort((left, right) =>
            checkpointTime(right).localeCompare(checkpointTime(left)))
          .find((job) =>
            !safeSessionGet(checkpointPromptKey(ownerKey, job)));
        setPendingResume(candidate ?? null);
      })
      .catch((discoveryError) => {
        if (active) {
          setResumeError(
            `Could not check for saved runs: ${String(discoveryError)}`,
          );
        }
      })
      .finally(() => {
        if (active) setResumeDiscoveryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [auth?.loading, ownerKey]);

  useEffect(() => {
    if (!pendingResume?.generation_running) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const fresh = await api.getUploadJob("concepts", pendingResume.id);
        if (!active) return;
        const freshSummary = resumableCheckpointFromJob(fresh);
        if (fresh.generation_running) {
          setPendingResume(freshSummary);
          timer = window.setTimeout(poll, 3000);
          return;
        }
        if (fresh.checkpoint_available) {
          setPendingResume(freshSummary);
          return;
        }
        acknowledgeCheckpointPrompt(ownerKey, freshSummary);
        setPendingResume(null);
        setResumeJob(fresh);
        setPath(fresh.learning_kind === "pre" ? "pre" : "post");
      } catch (pollError) {
        if (active) {
          setResumeError(
            `Could not refresh the active run: ${String(pollError)}`,
          );
          timer = window.setTimeout(poll, 5000);
        }
      }
    };
    timer = window.setTimeout(poll, 3000);
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [ownerKey, pendingResume?.generation_running, pendingResume?.id]);

  function choosePath(nextPath: Exclude<Path, null>) {
    setResumeJob(null);
    setPath(nextPath);
  }

  function keepCheckpointForLater(job: ResumableCheckpoint) {
    acknowledgeCheckpointPrompt(ownerKey, job);
    setPendingResume(null);
  }

  async function resumeCheckpoint(job: ResumableCheckpoint) {
    setResumeBusy(true);
    setResumeError(null);
    try {
      // Discovery intentionally returns no source text or logs. Fetch the full
      // owner-scoped job only after the user chooses Resume.
      const fullJob = await api.getUploadJob("concepts", job.id);
      acknowledgeCheckpointPrompt(ownerKey, job);
      setPendingResume(null);
      setResumeJob(fullJob);
      setPath(fullJob.learning_kind === "pre" ? "pre" : "post");
    } catch (resumeFailure) {
      setResumeError(`Could not restore the saved run: ${String(resumeFailure)}`);
    } finally {
      setResumeBusy(false);
    }
  }

  async function discardCheckpoint(job: ResumableCheckpoint) {
    setResumeBusy(true);
    setResumeError(null);
    try {
      await api.clearConceptCheckpoint(job.id);
      acknowledgeCheckpointPrompt(ownerKey, job);
      setPendingResume(null);
    } catch (discardError) {
      setResumeError(`Could not discard the checkpoint: ${String(discardError)}`);
    } finally {
      setResumeBusy(false);
    }
  }

  return (
    <>
      <h1>Build Concepts</h1>
      <div className="subtitle">
        Generate concepts from documents (Post Learning) or derive prerequisite
        concepts (Pre Learning). Output is written to the Bulk Import workbook.
      </div>
      {resumeDiscoveryLoading && (
        <div className="muted" role="status" style={{ marginBottom: 12 }}>
          Checking your saved concept runs…
        </div>
      )}

      {!path && (
        <div className="grid cols-2">
          <button
            className="module-card"
            disabled={resumeDiscoveryLoading}
            onClick={() => choosePath("post")}
          >
            <div className="module-title">1 · Post Learning</div>
            <div className="module-desc">
              Upload a document → convert to MMD → parse concepts → deposit under a chapter.
            </div>
          </button>
          <button
            className="module-card"
            disabled={resumeDiscoveryLoading}
            onClick={() => choosePath("pre")}
          >
            <div className="module-title">2 · Pre Learning</div>
            <div className="module-desc">
              Upload a document, or derive pre-learning concepts from one or more
              existing Post Learning chapters.
            </div>
          </button>
        </div>
      )}

      {path && (
        <button
          className="ghost"
          onClick={() => {
            setPath(null);
            setResumeJob(null);
          }}
          style={{ marginBottom: 16 }}
        >
          ← Back to options
        </button>
      )}
      {path === "post" && (
        <PostLearningFlow
          bookSources={bookSources}
          initialJob={
            resumeJob?.learning_kind === "post" ? resumeJob : null
          }
        />
      )}
      {path === "pre" && (
        <PreLearningFlow
          bookSources={bookSources}
          initialUploadJob={
            resumeJob?.learning_kind === "pre" ? resumeJob : null
          }
        />
      )}
      {resumeError && !pendingResume && (
        <div className="error-box" style={{ marginTop: 16 }}>
          {resumeError}
        </div>
      )}
      {pendingResume && (
        <ResumeCheckpointPrompt
          job={pendingResume}
          busy={resumeBusy}
          error={resumeError}
          onResume={() => void resumeCheckpoint(pendingResume)}
          onKeep={() => keepCheckpointForLater(pendingResume)}
          onDiscard={() => void discardCheckpoint(pendingResume)}
        />
      )}
    </>
  );
}

/* ----------------------------- post learning ----------------------------- */

function PostLearningFlow({
  bookSources,
  initialJob,
}: {
  bookSources: string[];
  initialJob: UploadJob | null;
}) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(initialJob);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [resultResumed, setResultResumed] = useState(false);
  const [treeReload, setTreeReload] = useState(0);
  // Read-only record of a semantic issue the run carried into its release.
  // It never gates the page: Aegis does not ask the user to choose mid-run.
  const [carriedIssue, setCarriedIssue] =
    useState<PendingSemanticDecision | null>(
      initialJob?.pending_decision ?? null,
    );

  useEffect(() => {
    if (initialJob) {
      setJob(initialJob);
      setCarriedIssue(initialJob.pending_decision ?? null);
    }
  }, [initialJob]);

  const handleJob = useCallback((nextJob: UploadJob | null) => {
    setJob(nextJob);
    setCarriedIssue(nextJob?.pending_decision ?? null);
    setError(null);
    setResult(null);
  }, []);

  async function generate() {
    if (!job || !scope) return;
    const resumedFromCheckpoint = Boolean(job.checkpoint_available);
    setBusy(true);
    setError(null);
    setResult(null);
    setCarriedIssue(null);
    setResultResumed(resumedFromCheckpoint);
    try {
      const data = await run<Record<string, unknown>>(
        "Post Learning — generating concepts",
        api.paths.postLearningGenerate(job.id),
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
        {
          cumulative: true,
          resumed: resumedFromCheckpoint,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
        {
          module: "concepts",
          jobId: job.id,
          // The run finished while the connection was down: rebuild the
          // fields the result panel reads from the completed job itself.
          recoverResult: async () => {
            const finished = await api.getUploadJob("concepts", job.id);
            return {
              status: "generated",
              reattached: true,
              job_id: finished.id,
              openai_usage: finished.openai_usage,
              pending_decision: finished.pending_decision,
            } as Record<string, unknown>;
          },
        },
      );
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // The result remains usable even if refreshing the completed job fails.
      }
      // A semantic issue is carried into the release, never turned back into
      // a mid-run question. Anything still attached is shown read-only.
      setCarriedIssue(
        pendingDecisionFrom(data) ?? refreshedJob?.pending_decision ?? null,
      );
      setResult(data);
    } catch (e) {
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // Keep the generation error visible if refreshing job state also fails.
      }
      setCarriedIssue(refreshedJob?.pending_decision ?? null);
      setError(formatGenerationError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">1 · Upload document</div>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        bookSources={bookSources}
        externalJob={job}
        disabled={busy}
        onJob={handleJob}
      />
      {!result && (
        <ApiUsageSummary
          usage={job?.openai_usage}
          filename={job?.filename}
          fileLabel="Source file"
          cumulative
          resumed={Boolean(job?.checkpoint_available)}
        />
      )}

      {job && job.status === "converted" && (
        <>
          <div className="section-title">2 · Deposit concepts under a chapter</div>
          <div className="card">
            <DirectoryPicker
              onScope={setScope}
              chapterOnly
              reloadSignal={treeReload}
              initialChapterIdentity={checkpointTargetIdentity(job)}
            />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `Chapter: ${scope.label}` : "Pick a chapter"}</span>
              <div className="spacer" />
              {(
                <button disabled={!scope || busy} onClick={generate}>
                  {job.checkpoint_available
                    ? `Resume from ${Math.round(
                      (job.checkpoint_progress ?? 0) * 100,
                    )}% checkpoint`
                    : "Parse & generate concepts"}
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {carriedIssue && (
        <CarriedSemanticIssue issue={carriedIssue} />
      )}
      {result && job && <ConceptReviewPanel jobId={job.id} />}
      {error && (
        <div className="error-box" style={{ marginTop: 16 }}>{error}</div>
      )}
      {result && (
        <ConceptResult
          result={result}
          filename={job?.filename}
          resumed={resultResumed}
        />
      )}
    </>
  );
}

/* ----------------------------- pre learning ----------------------------- */

function PreLearningFlow({
  bookSources,
  initialUploadJob,
}: {
  bookSources: string[];
  initialUploadJob: UploadJob | null;
}) {
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
        ? (
          <PreLearningUpload
            bookSources={bookSources}
            initialJob={initialUploadJob}
          />
        )
        : <PreLearningExisting bookSources={bookSources} />}
    </>
  );
}

function PreLearningUpload({
  bookSources,
  initialJob,
}: {
  bookSources: string[];
  initialJob: UploadJob | null;
}) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(initialJob);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [resultResumed, setResultResumed] = useState(false);
  const [treeReload, setTreeReload] = useState(0);
  // Read-only record of a semantic issue the run carried into its release.
  // It never gates the page: Aegis does not ask the user to choose mid-run.
  const [carriedIssue, setCarriedIssue] =
    useState<PendingSemanticDecision | null>(
      initialJob?.pending_decision ?? null,
    );

  useEffect(() => {
    if (initialJob) {
      setJob(initialJob);
      setCarriedIssue(initialJob.pending_decision ?? null);
    }
  }, [initialJob]);

  const handleJob = useCallback((nextJob: UploadJob | null) => {
    setJob(nextJob);
    setCarriedIssue(nextJob?.pending_decision ?? null);
    setError(null);
    setResult(null);
  }, []);

  async function generate() {
    if (!job || !scope) return;
    const resumedFromCheckpoint = Boolean(job.checkpoint_available);
    setBusy(true);
    setError(null);
    setResult(null);
    setCarriedIssue(null);
    setResultResumed(resumedFromCheckpoint);
    try {
      const data = await run<Record<string, unknown>>(
        "Pre Learning — generating concepts",
        api.paths.preLearningGenerate(job.id),
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
        {
          cumulative: true,
          resumed: resumedFromCheckpoint,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
        {
          module: "concepts",
          jobId: job.id,
          // The run finished while the connection was down: rebuild the
          // fields the result panel reads from the completed job itself.
          recoverResult: async () => {
            const finished = await api.getUploadJob("concepts", job.id);
            return {
              status: "generated",
              reattached: true,
              job_id: finished.id,
              openai_usage: finished.openai_usage,
              pending_decision: finished.pending_decision,
            } as Record<string, unknown>;
          },
        },
      );
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // The result remains usable even if refreshing the completed job fails.
      }
      // A semantic issue is carried into the release, never turned back into
      // a mid-run question. Anything still attached is shown read-only.
      setCarriedIssue(
        pendingDecisionFrom(data) ?? refreshedJob?.pending_decision ?? null,
      );
      setResult(data);
    } catch (e) {
      let refreshedJob: UploadJob | null = null;
      try {
        refreshedJob = await api.getUploadJob("concepts", job.id);
        setJob(refreshedJob);
      } catch {
        // Keep the generation error visible if refreshing job state also fails.
      }
      setCarriedIssue(refreshedJob?.pending_decision ?? null);
      setError(formatGenerationError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="section-title">1 · Upload document</div>
      <DocumentUpload
        module="concepts"
        conceptKind="pre"
        bookSources={bookSources}
        externalJob={job}
        disabled={busy}
        onJob={handleJob}
      />
      {!result && (
        <ApiUsageSummary
          usage={job?.openai_usage}
          filename={job?.filename}
          fileLabel="Source file"
          cumulative
          resumed={Boolean(job?.checkpoint_available)}
        />
      )}
      {job && job.status === "converted" && (
        <>
          <div className="section-title">2 · Deposit pre-learning concepts under a chapter</div>
          <div className="card">
            <DirectoryPicker
              onScope={setScope}
              chapterOnly
              reloadSignal={treeReload}
              initialChapterIdentity={checkpointTargetIdentity(job)}
            />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            <div className="row" style={{ marginTop: 12 }}>
              <span className="muted">{scope ? `Chapter: ${scope.label}` : "Pick a chapter"}</span>
              <div className="spacer" />
              {(
                <button disabled={!scope || busy} onClick={generate}>
                  {job.checkpoint_available
                    ? `Resume from ${Math.round(
                      (job.checkpoint_progress ?? 0) * 100,
                    )}% checkpoint`
                    : "Generate pre-learning concepts"}
                </button>
              )}
            </div>
          </div>
        </>
      )}
      {carriedIssue && (
        <CarriedSemanticIssue issue={carriedIssue} />
      )}
      {result && job && <ConceptReviewPanel jobId={job.id} />}
      {error && (
        <div className="error-box" style={{ marginTop: 16 }}>{error}</div>
      )}
      {result && (
        <ConceptResult
          result={result}
          filename={job?.filename}
          resumed={resultResumed}
        />
      )}
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

function CarriedSemanticIssue({ issue }: { issue: PendingSemanticDecision }) {
  // Aegis no longer asks the user to resolve a semantic issue during a run.
  // The issue travels into the release with its evidence, so this panel is
  // deliberately read-only -- there is nothing here to click.
  const topic = issue.item?.topic ?? "";
  const typeTitle = issue.item?.type_title ?? "";
  const qids = issue.item?.qids ?? [];
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="section-title">Semantic issue carried into the release</div>
      <p className="muted">
        Generation did not stop for this. The issue and its source evidence are
        included in the release workbook and the diagnostics export, where the
        affected rows are highlighted.
      </p>
      <dl className="mono">
        {topic && (
          <>
            <dt>Topic</dt>
            <dd>{topic}</dd>
          </>
        )}
        {typeTitle && (
          <>
            <dt>Type</dt>
            <dd>{typeTitle}</dd>
          </>
        )}
        {qids.length > 0 && (
          <>
            <dt>QIDs</dt>
            <dd>{qids.join(", ")}</dd>
          </>
        )}
        <dt>Reference</dt>
        <dd>{issue.decision_id}</dd>
      </dl>
      {issue.conflict && <p className="mono">{issue.conflict}</p>}
    </div>
  );
}














function pendingDecisionFrom(
  result: Record<string, unknown>,
): PendingSemanticDecision | null {
  const candidate = result.pending_decision;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return null;
  }
  const decisionId = (candidate as Record<string, unknown>).decision_id;
  if (typeof decisionId !== "string" || !decisionId.trim()) return null;
  return candidate as PendingSemanticDecision;
}























function ResumeCheckpointPrompt({
  job,
  busy,
  error,
  onResume,
  onKeep,
  onDiscard,
}: {
  job: ResumableCheckpoint;
  busy: boolean;
  error: string | null;
  onResume: () => void;
  onKeep: () => void;
  onDiscard: () => void;
}) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, [job.id]);
  const running = Boolean(job.generation_running);
  return (
    <div className="resume-dialog-backdrop">
      <section
        className="card resume-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="resume-dialog-title"
        aria-describedby="resume-dialog-description"
      >
        <div>
          <span className={`badge ${running ? "yellow" : "green"}`}>
            {running ? "Run is still active" : "Saved run found"}
          </span>
          <h2 id="resume-dialog-title" ref={headingRef} tabIndex={-1}>
            {running ? "Generation is already running" : "Resume this concept run?"}
          </h2>
          <p id="resume-dialog-description" className="muted">
            {running
              ? "Aegis is polling this run. It will not start a duplicate or "
                + "make another billable generation request."
              : "Resume restores the source and saved chapter selection. It "
                + "does not call OpenAI; generation starts only after you "
                + "review the destination and click the generation button."}
          </p>
        </div>
        <dl className="resume-details">
          <div>
            <dt>File</dt>
            <dd>{job.filename}</dd>
          </div>
          <div>
            <dt>Learning flow</dt>
            <dd>{job.learning_kind === "pre" ? "Pre Learning" : "Post Learning"}</dd>
          </div>
          <div>
            <dt>Saved stage</dt>
            <dd>{formatCheckpointStage(job.checkpoint_stage)}</dd>
          </div>
          <div>
            <dt>Progress</dt>
            <dd>{Math.round((job.checkpoint_progress ?? 0) * 100)}%</dd>
          </div>
          <div>
            <dt>Saved</dt>
            <dd>{formatCheckpointTime(job.checkpoint_saved_at)}</dd>
          </div>
          <div>
            <dt>Target</dt>
            <dd>{formatCheckpointTarget(job.checkpoint_target_identity)}</dd>
          </div>
        </dl>
        {error && <div className="error-box">{error}</div>}
        <div className="row resume-actions">
          {!running && (
            <>
              <button type="button" disabled={busy} onClick={onResume}>
                Resume
              </button>
              <button
                className="ghost"
                type="button"
                disabled={busy}
                onClick={onDiscard}
              >
                {busy ? "Discarding…" : "Discard"}
              </button>
            </>
          )}
          {running && (
            <button type="button" disabled>
              Run is still active
            </button>
          )}
          <button
            className="ghost"
            type="button"
            disabled={busy}
            onClick={onKeep}
          >
            Keep for later
          </button>
        </div>
      </section>
    </div>
  );
}

function checkpointTime(job: ResumableCheckpoint): string {
  return job.checkpoint_saved_at || job.created_at || "";
}

function resumableCheckpointFromJob(job: UploadJob): ResumableCheckpoint {
  return {
    id: job.id,
    module: job.module,
    learning_kind: job.learning_kind,
    filename: job.filename,
    status: job.status,
    checkpoint_available: Boolean(job.checkpoint_available),
    checkpoint_stage: job.checkpoint_stage,
    checkpoint_saved_at: job.checkpoint_saved_at,
    checkpoint_progress: job.checkpoint_progress,
    checkpoint_target_identity: job.checkpoint_target_identity,
    generation_running: Boolean(job.generation_running),
    created_at: job.created_at,
  };
}

function checkpointTargetIdentity(
  job: UploadJob,
): Record<string, string> | undefined {
  const identity = job.checkpoint_target_identity;
  return job.checkpoint_available
    && identity
    && Object.keys(identity).length > 0
    ? identity
    : undefined;
}

function checkpointPromptKey(
  ownerKey: string,
  job: ResumableCheckpoint,
): string {
  const revision = checkpointTime(job) || job.checkpoint_stage || "checkpoint";
  return [
    "aegis-resume-prompt",
    encodeURIComponent(ownerKey),
    job.learning_kind || "concepts",
    String(job.id),
    encodeURIComponent(revision),
  ].join(":");
}

function safeSessionGet(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSessionSet(key: string): void {
  try {
    window.sessionStorage.setItem(key, "acknowledged");
  } catch {
    // The prompt still behaves once for the mounted page when storage is blocked.
  }
}

function acknowledgeCheckpointPrompt(
  ownerKey: string,
  job: ResumableCheckpoint,
) {
  safeSessionSet(checkpointPromptKey(ownerKey, job));
}

function formatCheckpointStage(stage?: string): string {
  return (stage || "saved stage").replace(/_/g, " ");
}

function formatCheckpointTime(value?: string): string {
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString();
}

function formatCheckpointTarget(
  identity?: Record<string, string>,
): string {
  if (!identity) return "Select the matching chapter";
  const fields = [
    "board",
    "grade",
    "subject",
    "unit",
    "chapter_title",
    "chapter_code",
  ];
  const values = fields
    .map((field) => identity[field]?.trim())
    .filter((value): value is string => Boolean(value));
  return values.length ? values.join(" / ") : "Select the matching chapter";
}

function formatGenerationError(error: unknown): string {
  const message = String(error);
  if (
    /\b409\b/.test(message)
    || /already (?:running|in progress)/i.test(message)
    || /generation .* in progress/i.test(message)
  ) {
    return "This run is already active in another tab or laptop. Aegis did "
      + "not start a duplicate. Wait for it to finish, then refresh the job.";
  }
  return message;
}

function ConceptResult({
  result,
  filename,
  resumed = false,
}: {
  result: Record<string, unknown>;
  filename?: string;
  resumed?: boolean;
}) {
  const ids = (result.concept_ids as number[] | undefined) ?? [];
  const jobId = result.job_id as number | undefined;
  const inventoryItems = (result.inventory_items as number | undefined) ?? 0;
  const usage = result.openai_usage as OpenAIUsage | undefined;
  return (
    <div className="card success-card" style={{ marginTop: 16 }}>
      <strong>Concepts written to the Bulk Import workbook (append-only)</strong>
      <ApiUsageSummary
        usage={usage}
        filename={filename}
        fileLabel="Source file"
        cumulative={jobId != null || Boolean(filename)}
        resumed={resumed}
      />
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
