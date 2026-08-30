import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  isNonTransientStatus,
  type ModelProviderInfo,
} from "../api/client";
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

type Path = null | "post";

export default function BuildConcepts() {
  const auth = useOptionalAuth();
  const { watch: watchRun } = useRunConsole();
  const [path, setPath] = useState<Path>("post");
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
    let active = true;
    setResumeDiscoveryLoading(true);
    setResumeError(null);
    // One lane only. `learning_kind` stays in the route contract (there is no
    // migration path for the stored column), but Build Concepts no longer
    // initiates a pre-learning discovery call.
    api.resumableConceptCheckpoints("post")
      .then((post) => {
        if (!active) return;
        // Marked discovered only once a result APPLIES: marking before the
        // fetch resolves let an unmounted first attempt (StrictMode's dev
        // double-mount, a fast navigation away) permanently swallow
        // discovery for this owner.
        discoveredForUserRef.current = ownerKey;
        const candidate = post.items
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
        setPath("post");
      } catch (pollError) {
        if (active) {
          setResumeError(
            `Could not refresh the active run: ${String(pollError)}`,
          );
          // A non-transient refusal (session expired, job gone) never
          // improves on retry — stop the poll instead of spinning every
          // 5s in the background for as long as the tab lives.
          if (!isNonTransientStatus(pollError)) {
            timer = window.setTimeout(poll, 5000);
          }
        }
      }
    };
    timer = window.setTimeout(poll, 3000);
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [ownerKey, pendingResume?.generation_running, pendingResume?.id]);

  function keepCheckpointForLater(job: ResumableCheckpoint) {
    acknowledgeCheckpointPrompt(ownerKey, job);
    setPendingResume(null);
  }

  function watchRunningJob(job: ResumableCheckpoint) {
    // Attach-only: the console tails the durable journal from the start
    // (the replay rebuilds the stage cards with real times and costs).
    // Nothing is POSTed, nothing resumes, nothing bills.
    acknowledgeCheckpointPrompt(ownerKey, job);
    setPendingResume(null);
    void watchRun(`Watching: ${job.filename}`, {
      module: "concepts",
      jobId: job.id,
    })
      .then(async () => {
        // The watched run finished: land on the download-and-review
        // page exactly as a run started from this tab would. A stopped
        // worker leaves the console's note and stays put — the job
        // status decides, not the watcher.
        const fullJob = await api.getUploadJob("concepts", job.id);
        if (
          fullJob.status === "generated"
          || fullJob.status === "released"
        ) {
          setResumeJob(fullJob);
          setPath("post");
        }
      })
      .catch(() => {
        /* the console already carries the failure line */
      });
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
      setPath("post");
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
        Generate concepts from documents. Output is written to the Bulk Import
        workbook.
      </div>
      {resumeDiscoveryLoading && (
        <div className="row muted mb-12" role="status">
          <span className="spinner" aria-hidden="true" />
          Checking your saved concept runs…
        </div>
      )}


      {path === "post" && (
        <PostLearningFlow
          bookSources={bookSources}
          initialJob={
            resumeJob?.learning_kind === "post" ? resumeJob : null
          }
        />
      )}
      {resumeError && !pendingResume && (
        <div className="error-box mt-16">
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
          onWatch={() => watchRunningJob(pendingResume)}
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
  // The one-shot chain (upload → convert → generate) reads the scope when
  // conversion FINISHES, not when the upload was clicked — a ref avoids a
  // stale closure over the render that started the chain.
  const scopeRef = useRef<Scope | null>(null);
  useEffect(() => {
    scopeRef.current = scope;
  }, [scope]);
  const [busy, setBusy] = useState(false);
  const [modelProvider, setModelProviderInfo] =
    useState<ModelProviderInfo | null>(null);
  const [modelProviderError, setModelProviderError] =
    useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api.getModelProvider()
      .then((info) => {
        if (active) setModelProviderInfo(info);
      })
      .catch(() => {
        /* the selector simply stays hidden when the endpoint is unavailable */
      });
    return () => {
      active = false;
    };
  }, []);

  const chooseModelProvider = useCallback(async (provider: string) => {
    setModelProviderError(null);
    try {
      setModelProviderInfo(await api.setModelProvider(provider));
    } catch (choiceError) {
      setModelProviderError(String(choiceError));
    }
  }, []);
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

  async function generate(target?: UploadJob) {
    const runJob = target ?? job;
    const runScope = scopeRef.current;
    if (!runJob || !runScope) return;
    const resumedFromCheckpoint = Boolean(runJob.checkpoint_available);
    setBusy(true);
    setError(null);
    setResult(null);
    setCarriedIssue(null);
    setResultResumed(resumedFromCheckpoint);
    try {
      const data = await run<Record<string, unknown>>(
        "Post Learning — generating concepts",
        api.paths.postLearningGenerate(runJob.id),
        { body: JSON.stringify({ target_chapter_id: runScope.ids[0] }) },
        {
          cumulative: true,
          resumed: resumedFromCheckpoint,
          filename: runJob.filename,
          fileLabel: "Source file",
          initialUsage: runJob.openai_usage,
        },
        {
          module: "concepts",
          jobId: runJob.id,
          // The run finished while the connection was down: rebuild the
          // fields the result panel reads from the completed job itself.
          recoverResult: async () => {
            const finished = await api.getUploadJob("concepts", runJob.id);
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
        refreshedJob = await api.getUploadJob("concepts", runJob.id);
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
        refreshedJob = await api.getUploadJob("concepts", runJob.id);
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

  // Every run parameter is chosen up front (owner request, 2026-08-29):
  // chapter target, model provider, source book, and the file sit in one
  // view before anything is uploaded, and one action runs the whole
  // upload → parse → generate chain. The panel stays MOUNTED for every
  // pre-generation state — no job, a restored not-yet-parsed upload, a
  // parse in flight, a converted-but-ungenerated job — because
  // unmounting it between "uploaded" and "converted" made the whole page
  // visibly reset twice per run (owner report, 2026-08-30: "this page
  // loads again"). It collapses only once generation has produced output.
  const parameterPanelOpen = !job
    || (job.status !== "generated" && job.status !== "released");
  const oneShotReady = !job && Boolean(scope);

  return (
    <>
      {parameterPanelOpen && (
        <>
          <div className="section-title">1 · Choose the run parameters</div>
          <div className="card">
            <DirectoryPicker
              onScope={setScope}
              chapterOnly
              reloadSignal={treeReload}
              initialChapterIdentity={job ? checkpointTargetIdentity(job) : undefined}
            />
            <SyllabusUploader disabled={busy} onLoaded={() => setTreeReload((n) => n + 1)} />
            {modelProvider && (
              <div className="field mt-16">
                <label className="field-label" htmlFor="model-provider-select">
                  Model provider
                </label>
                <div className="row">
                  <select
                    id="model-provider-select"
                    value={modelProvider.provider}
                    disabled={busy}
                    onChange={(event) =>
                      void chooseModelProvider(event.target.value)}
                  >
                    <option value="openai" disabled={!modelProvider.openai_available}>
                      OpenAI ({modelProvider.openai_model})
                    </option>
                    <option value="gemini" disabled={!modelProvider.gemini_available}>
                      Gemini ({modelProvider.gemini_model})
                    </option>
                  </select>
                  <span className="hint">
                    {modelProvider.note
                      || `Next run uses ${modelProvider.model}.`}
                  </span>
                </div>
              </div>
            )}
            {modelProviderError && (
              <div className="error-box mb-12">{modelProviderError}</div>
            )}
            <div className="row mt-16">
              <span className="muted">
                {scope ? `Chapter: ${scope.label}` : "Pick a chapter"}
              </span>
              <div className="spacer" />
              {job && job.status === "converted" && (
                <button
                  className="primary"
                  disabled={!scope || busy}
                  onClick={() => void generate()}
                >
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

      <div className="section-title">2 · Upload document</div>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        bookSources={bookSources}
        externalJob={job}
        disabled={busy}
        onJob={handleJob}
        uploadLabel={oneShotReady ? "Upload, parse & generate" : undefined}
        uploadHint={oneShotReady
          ? "One action runs the whole chain: the file is stored, parsed, "
            + "and generation starts against the chapter you picked "
            + "above. Watch the Console for live progress."
          : "Uploading stores the file and starts its conversion right away "
            + "— watch the Console for parse progress. Pick a chapter above "
            + "first to run upload, parse, and generation in one go."}
        onConverted={(convertedJob) => {
          // Continue the chain only when a chapter was chosen up front.
          if (scopeRef.current) void generate(convertedJob);
        }}
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

      {carriedIssue && (
        <CarriedSemanticIssue issue={carriedIssue} />
      )}
      {job
        && (result
          || job.status === "generated"
          || job.status === "released")
        && <ConceptReviewPanel jobId={job.id} />}
      {error && (
        <div className="error-box mt-16">{error}</div>
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

function CarriedSemanticIssue({ issue }: { issue: PendingSemanticDecision }) {
  // Aegis no longer asks the user to resolve a semantic issue during a run.
  // The issue travels into the release with its evidence, so this panel is
  // deliberately read-only -- there is nothing here to click.
  const topic = issue.item?.topic ?? "";
  const typeTitle = issue.item?.type_title ?? "";
  const qids = issue.item?.qids ?? [];
  return (
    <div className="card mt-16">
      <div className="section-title">Semantic issue carried into the release</div>
      <p className="muted">
        Generation did not stop for this. The issue and its source evidence are
        included in the release workbook and the diagnostics export, where the
        affected rows are highlighted.
      </p>
      <dl className="kv">
        {topic && (
          <div>
            <dt>Topic</dt>
            <dd>{topic}</dd>
          </div>
        )}
        {typeTitle && (
          <div>
            <dt>Type</dt>
            <dd>{typeTitle}</dd>
          </div>
        )}
        {qids.length > 0 && (
          <div>
            <dt>QIDs</dt>
            <dd className="mono">{qids.join(", ")}</dd>
          </div>
        )}
        <div>
          <dt>Reference</dt>
          <dd className="mono">{issue.decision_id}</dd>
        </div>
      </dl>
      {issue.conflict && <p className="muted mt-12">{issue.conflict}</p>}
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
  onWatch,
}: {
  job: ResumableCheckpoint;
  busy: boolean;
  error: string | null;
  onResume: () => void;
  onKeep: () => void;
  onDiscard: () => void;
  onWatch: () => void;
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
              <button className="primary" type="button" disabled={busy} onClick={onResume}>
                Resume
              </button>
              <button
                className="ghost"
                type="button"
                disabled={busy}
                onClick={onDiscard}
              >
                {busy && <><span className="spinner" aria-hidden="true" />{" "}</>}
                {busy ? "Discarding…" : "Discard"}
              </button>
            </>
          )}
          {running && (
            <button className="primary" type="button" onClick={onWatch}>
              Watch live
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

/* Durable acknowledgement: "Keep for later" must hold across browser
   sessions, not just tabs — the key is revision-aware (checkpoint saved_at),
   so a genuinely NEW checkpoint on the same job prompts again. */
function safeSessionGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSessionSet(key: string): void {
  try {
    window.localStorage.setItem(key, "acknowledged");
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
  const jobId = result.job_id as number | undefined;
  const usage = result.openai_usage as OpenAIUsage | undefined;
  const status = typeof result.status === "string" ? result.status : "";
  const rowCount = typeof result.row_count === "number" ? result.row_count : null;
  const issueCount = typeof result.issue_count === "number" ? result.issue_count : null;
  return (
    <div className="card success-card mt-16">
      <div className="row">
        <strong>Concepts written to the Bulk Import workbook (append-only)</strong>
        {status && <span className="badge green">{status}</span>}
      </div>
      {(rowCount !== null || issueCount !== null) && (
        <div className="row mt-8">
          {rowCount !== null && (
            <span className="muted">
              rows released for review: <strong>{rowCount}</strong>
            </span>
          )}
          {issueCount !== null && (
            <span className="muted">
              issues attached: <strong>{issueCount}</strong>
            </span>
          )}
        </div>
      )}
      <ApiUsageSummary
        usage={usage}
        filename={filename}
        fileLabel="Source file"
        cumulative={jobId != null || Boolean(filename)}
        resumed={resumed}
      />
      <div className="muted mt-12">
        The four run outputs (Concept and Master Files for both lanes)
        download from the Run outputs section above; review and publishing
        stay separate, explicit acts.
      </div>
      <details className="mt-12">
        <summary>Raw result JSON</summary>
        <pre className="mono mt-8">{JSON.stringify(result, null, 2)}</pre>
      </details>
    </div>
  );
}
