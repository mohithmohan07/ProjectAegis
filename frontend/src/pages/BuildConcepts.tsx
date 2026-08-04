import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useOptionalAuth } from "../Auth";
import { useAsync } from "../hooks";
import { useRunConsole } from "../RunConsole";
import DirectoryPicker from "../components/DirectoryPicker";
import DocumentUpload from "../components/DocumentUpload";
import SyllabusUploader from "../components/SyllabusUploader";
import ApiUsageSummary from "../components/ApiUsageSummary";
import type {
  OpenAIUsage,
  ResumableCheckpoint,
  Scope,
  UploadJob,
} from "../types";

type Path = null | "post" | "pre";
type LearningKind = "post" | "pre";
type ReleaseResult = Record<string, unknown>;

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${api.base}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers as Record<string, string> | undefined),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json() as { detail?: unknown };
      if (body.detail) {
        detail = typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
      }
    } catch {
      // Keep the status text.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function releaseBase(jobId: number): string {
  return `/build-concepts/releases/${jobId}`;
}

function absoluteApiUrl(path: string): string {
  return `${api.base}${path}`;
}

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
      .catch((error) => {
        if (active) {
          setResumeError(`Could not check saved runs: ${String(error)}`);
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
        const summary = resumableCheckpointFromJob(fresh);
        if (fresh.generation_running) {
          setPendingResume(summary);
          timer = window.setTimeout(poll, 3000);
          return;
        }
        acknowledgeCheckpointPrompt(ownerKey, summary);
        setPendingResume(null);
        setResumeJob(fresh);
        setPath(fresh.learning_kind === "pre" ? "pre" : "post");
      } catch (error) {
        if (active) {
          setResumeError(`Could not refresh the active run: ${String(error)}`);
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

  async function resumeCheckpoint(job: ResumableCheckpoint) {
    setResumeBusy(true);
    setResumeError(null);
    try {
      const fullJob = await api.getUploadJob("concepts", job.id);
      acknowledgeCheckpointPrompt(ownerKey, job);
      setPendingResume(null);
      setResumeJob(fullJob);
      setPath(fullJob.learning_kind === "pre" ? "pre" : "post");
    } catch (error) {
      setResumeError(`Could not restore the saved run: ${String(error)}`);
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
    } catch (error) {
      setResumeError(`Could not discard the checkpoint: ${String(error)}`);
    } finally {
      setResumeBusy(false);
    }
  }

  return (
    <>
      <h1>Build Concepts</h1>
      <div className="subtitle">
        Generate a reviewable concept release from the source. Generation never
        writes to the database. Review the highlighted workbook first, then use
        the separate <strong>Upload to database</strong> action.
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <strong>Unattended generation contract</strong>
        <p className="muted" style={{ marginBottom: 0 }}>
          Aegis does not ask you to choose a concept, Type, Case, topic, BLK, or
          repair during generation. Unresolved items are carried into the output
          with row-level errors and the complete source context.
        </p>
      </div>

      {resumeDiscoveryLoading && (
        <div className="muted" role="status" style={{ marginBottom: 12 }}>
          Checking saved runs…
        </div>
      )}

      {!path && (
        <div className="grid cols-2">
          <button
            className="module-card"
            disabled={resumeDiscoveryLoading}
            onClick={() => {
              setResumeJob(null);
              setPath("post");
            }}
          >
            <div className="module-title">1 · Post Learning</div>
            <div className="module-desc">
              Upload source → generate concepts and reusable Types/Cases →
              download review release → upload separately.
            </div>
          </button>
          <button
            className="module-card"
            disabled={resumeDiscoveryLoading}
            onClick={() => {
              setResumeJob(null);
              setPath("pre");
            }}
          >
            <div className="module-title">2 · Pre Learning</div>
            <div className="module-desc">
              Upload a prerequisite source → generate a review release → upload
              separately after review.
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
        <UploadReleaseFlow
          kind="post"
          bookSources={bookSources}
          initialJob={resumeJob?.learning_kind === "post" ? resumeJob : null}
        />
      )}
      {path === "pre" && (
        <UploadReleaseFlow
          kind="pre"
          bookSources={bookSources}
          initialJob={resumeJob?.learning_kind === "pre" ? resumeJob : null}
        />
      )}

      {resumeError && !pendingResume && (
        <div className="error-box" style={{ marginTop: 16 }}>{resumeError}</div>
      )}
      {pendingResume && (
        <ResumeCheckpointPrompt
          job={pendingResume}
          busy={resumeBusy}
          error={resumeError}
          onResume={() => void resumeCheckpoint(pendingResume)}
          onKeep={() => {
            acknowledgeCheckpointPrompt(ownerKey, pendingResume);
            setPendingResume(null);
          }}
          onDiscard={() => void discardCheckpoint(pendingResume)}
        />
      )}
    </>
  );
}

function UploadReleaseFlow({
  kind,
  bookSources,
  initialJob,
}: {
  kind: LearningKind;
  bookSources: string[];
  initialJob: UploadJob | null;
}) {
  const { run } = useRunConsole();
  const [job, setJob] = useState<UploadJob | null>(initialJob);
  const [scope, setScope] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReleaseResult | null>(null);
  const [resultResumed, setResultResumed] = useState(false);
  const [treeReload, setTreeReload] = useState(0);

  useEffect(() => {
    if (initialJob) setJob(initialJob);
  }, [initialJob]);

  useEffect(() => {
    if (!job || !["released", "generated"].includes(job.status)) return;
    let active = true;
    requestJson<ReleaseResult>(releaseBase(job.id))
      .then((data) => {
        if (active) setResult(data);
      })
      .catch(() => {
        // A generated legacy job may not have a release. Keep its job state.
      });
    return () => {
      active = false;
    };
  }, [job?.id, job?.status]);

  const handleJob = useCallback((nextJob: UploadJob | null) => {
    setJob(nextJob);
    setError(null);
    setResult(null);
  }, []);

  async function generate() {
    if (!job || !scope) return;
    const resumed = Boolean(job.checkpoint_available);
    setBusy(true);
    setError(null);
    setResult(null);
    setResultResumed(resumed);
    try {
      const endpoint = kind === "post"
        ? api.paths.postLearningGenerate(job.id)
        : api.paths.preLearningGenerate(job.id);
      const data = await run<ReleaseResult>(
        `${kind === "post" ? "Post" : "Pre"} Learning — generating review release`,
        endpoint,
        { body: JSON.stringify({ target_chapter_id: scope.ids[0] }) },
        {
          cumulative: true,
          resumed,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
      );
      setResult(data);
      try {
        setJob(await api.getUploadJob("concepts", job.id));
      } catch {
        // The release result is complete even when the refresh fails.
      }
    } catch (failure) {
      setError(formatGenerationError(failure));
      try {
        const refreshed = await api.getUploadJob("concepts", job.id);
        setJob(refreshed);
        if (refreshed.status === "released") {
          setResult(await requestJson<ReleaseResult>(releaseBase(job.id)));
          setError(null);
        }
      } catch {
        // Keep the original generation error and context-export link.
      }
    } finally {
      setBusy(false);
    }
  }

  const displayJob = job?.status === "released"
    ? { ...job, status: "generated" }
    : job;
  const readyToGenerate = job?.status === "converted";
  const hasTerminalRelease = Boolean(result) || job?.status === "released";

  return (
    <>
      <div className="section-title">1 · Upload and convert source</div>
      <DocumentUpload
        module="concepts"
        conceptKind={kind}
        bookSources={bookSources}
        externalJob={displayJob}
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

      {job && (readyToGenerate || hasTerminalRelease) && (
        <>
          <div className="section-title">
            2 · Choose destination metadata and create review release
          </div>
          <div className="card">
            <p className="muted" style={{ marginTop: 0 }}>
              The chapter selection is recorded in the release manifest. It does
              not deposit concepts. Database publication remains separate.
            </p>
            {!hasTerminalRelease && (
              <>
                <DirectoryPicker
                  onScope={setScope}
                  chapterOnly
                  reloadSignal={treeReload}
                  initialChapterIdentity={checkpointTargetIdentity(job)}
                />
                <SyllabusUploader
                  disabled={busy}
                  onLoaded={() => setTreeReload((value) => value + 1)}
                />
                <div className="row" style={{ marginTop: 12 }}>
                  <span className="muted">
                    {scope ? `Destination metadata: ${scope.label}` : "Pick a chapter"}
                  </span>
                  <div className="spacer" />
                  <button disabled={!scope || busy} onClick={generate}>
                    {busy
                      ? "Generating release…"
                      : job.checkpoint_available
                        ? `Resume autonomously from ${Math.round(
                          (job.checkpoint_progress ?? 0) * 100,
                        )}% and release output`
                        : "Generate review release"}
                  </button>
                </div>
              </>
            )}
          </div>
        </>
      )}

      {job && !result && (
        <div className="row" style={{ marginTop: 12 }}>
          <a href={absoluteApiUrl(`${releaseBase(job.id)}/context`)}>
            <button className="ghost" type="button">
              ⬇ Export current log + source evidence + BLKs + checkpoint
            </button>
          </a>
          <span className="muted">
            Available even when the run has not reached a final workbook.
          </span>
        </div>
      )}

      {error && (
        <div className="error-box" style={{ marginTop: 16 }}>
          <div>{error}</div>
          {job && (
            <a href={absoluteApiUrl(`${releaseBase(job.id)}/context`)}>
              Download the complete issue context
            </a>
          )}
        </div>
      )}

      {result && (
        <ConceptReleaseResult
          result={result}
          filename={job?.filename}
          resumed={resultResumed}
          onPublished={async () => {
            if (!job) return;
            try {
              setJob(await api.getUploadJob("concepts", job.id));
            } catch {
              // The publish result remains visible.
            }
          }}
        />
      )}
    </>
  );
}

function ConceptReleaseResult({
  result,
  filename,
  resumed,
  onPublished,
}: {
  result: ReleaseResult;
  filename?: string;
  resumed: boolean;
  onPublished: () => Promise<void>;
}) {
  const [publishBusy, setPublishBusy] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [publishResult, setPublishResult] = useState<ReleaseResult | null>(null);
  const jobId = Number(result.job_id || 0);
  const usage = result.openai_usage as OpenAIUsage | undefined;
  const databaseWritten = Boolean(
    publishResult?.database_written ?? result.database_written,
  );
  const uploadAllowed = Boolean(result.database_upload_allowed);
  const records = Number(result.records_released || 0);
  const errors = Number(result.error_count || 0);
  const warnings = Number(result.warning_count || 0);
  const workbookPath = String(
    result.release_workbook_url || `${releaseBase(jobId)}/workbook`,
  );
  const contextPath = String(
    result.context_bundle_url || `${releaseBase(jobId)}/context`,
  );
  const manifestPath = String(
    result.release_manifest_url || `${releaseBase(jobId)}/manifest`,
  );
  const uploadPath = String(
    result.upload_to_database_url
      || `${releaseBase(jobId)}/upload-to-database`,
  );
  const conceptIds = (
    (publishResult?.concept_ids || result.concept_ids || []) as number[]
  );

  async function publish() {
    setPublishBusy(true);
    setPublishError(null);
    try {
      const data = await requestJson<ReleaseResult>(uploadPath, {
        method: "POST",
      });
      setPublishResult(data);
      await onPublished();
    } catch (error) {
      setPublishError(String(error));
    } finally {
      setPublishBusy(false);
    }
  }

  return (
    <div className="card success-card" style={{ marginTop: 16 }}>
      <div className="row">
        <div>
          <span className="badge green">Review release created</span>
          <h2 style={{ marginBottom: 4 }}>Output released without database deposit</h2>
          <p className="muted" style={{ marginTop: 0 }}>
            {records} concept row(s) · {errors} error(s) · {warnings} warning(s)
          </p>
        </div>
        <div className="spacer" />
        <span className={`badge ${uploadAllowed ? "green" : "yellow"}`}>
          {uploadAllowed ? "Database-ready" : "Review required"}
        </span>
      </div>

      <ApiUsageSummary
        usage={usage}
        filename={filename}
        fileLabel="Source file"
        cumulative
        resumed={resumed}
      />

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <a href={absoluteApiUrl(workbookPath)}>
          <button type="button" style={{ width: "100%" }}>
            ⬇ Download highlighted review workbook
          </button>
        </a>
        <a href={absoluteApiUrl(contextPath)}>
          <button className="ghost" type="button" style={{ width: "100%" }}>
            ⬇ Download complete context ZIP
          </button>
        </a>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <a href={absoluteApiUrl(manifestPath)}>
          <button className="ghost" type="button">⬇ Release manifest</button>
        </a>
        {jobId > 0 && (
          <a href={api.inventoryCsvUrl(jobId)}>
            <button className="ghost" type="button">⬇ Question/Task Inventory</button>
          </a>
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <strong>Database publication</strong>
        <p className="muted">
          Generation has not written anything to the database. This separate
          action re-runs the deterministic concept, QID, Type/Case, evidence and
          grounding gates before publication. It makes no generation request.
        </p>
        {!uploadAllowed && !databaseWritten && (
          <div className="error-box">
            Upload is blocked because the release contains errors. The workbook
            is still complete and downloadable; fix or regenerate the highlighted
            rows, then create a clean release.
          </div>
        )}
        {publishError && <div className="error-box">{publishError}</div>}
        <div className="row">
          <button
            type="button"
            disabled={!uploadAllowed || publishBusy || databaseWritten}
            onClick={() => void publish()}
          >
            {databaseWritten
              ? "Uploaded to database"
              : publishBusy
                ? "Uploading…"
                : "Upload this release to database"}
          </button>
          <span className="muted">
            Nothing is uploaded automatically.
          </span>
        </div>
      </div>

      {databaseWritten && (
        <div className="semantic-decision-recorded" role="status">
          <div>
            <strong>Database upload completed.</strong>
            <p>
              The reviewed release passed the deposit gates and is now in the
              canonical Bulk Import database.
            </p>
          </div>
          {conceptIds.length > 0 && (
            <a href={api.exportConceptsUrl(conceptIds)}>
              <button type="button">⬇ Download uploaded concepts</button>
            </a>
          )}
        </div>
      )}

      <details style={{ marginTop: 12 }}>
        <summary>Release response</summary>
        <pre className="mono">{JSON.stringify(publishResult || result, null, 2)}</pre>
      </details>
    </div>
  );
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
      >
        <span className={`badge ${running ? "yellow" : "green"}`}>
          {running ? "Run active" : "Saved run found"}
        </span>
        <h2 id="resume-dialog-title" ref={headingRef} tabIndex={-1}>
          {running ? "Generation is already running" : "Continue this saved run?"}
        </h2>
        <p className="muted">
          Continuing restores the source and checkpoint. It does not ask you to
          make semantic selections. Aegis will either finish normally or release
          the latest materialized output with its complete context.
        </p>
        <dl className="resume-details">
          <div><dt>File</dt><dd>{job.filename}</dd></div>
          <div><dt>Flow</dt><dd>{job.learning_kind === "pre" ? "Pre" : "Post"} Learning</dd></div>
          <div><dt>Stage</dt><dd>{formatCheckpointStage(job.checkpoint_stage)}</dd></div>
          <div><dt>Progress</dt><dd>{Math.round((job.checkpoint_progress ?? 0) * 100)}%</dd></div>
          <div><dt>Saved</dt><dd>{formatCheckpointTime(job.checkpoint_saved_at)}</dd></div>
          <div><dt>Target</dt><dd>{formatCheckpointTarget(job.checkpoint_target_identity)}</dd></div>
        </dl>
        <a href={absoluteApiUrl(`${releaseBase(job.id)}/context`)}>
          <button className="ghost" type="button">
            ⬇ Export current issue context
          </button>
        </a>
        {error && <div className="error-box">{error}</div>}
        <div className="row resume-actions">
          {!running && (
            <>
              <button type="button" disabled={busy} onClick={onResume}>Continue</button>
              <button className="ghost" type="button" disabled={busy} onClick={onDiscard}>
                {busy ? "Discarding…" : "Discard checkpoint"}
              </button>
            </>
          )}
          {running && <button type="button" disabled>Run is active</button>}
          <button className="ghost" type="button" disabled={busy} onClick={onKeep}>
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
    // Best effort only.
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
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function formatCheckpointTarget(
  identity?: Record<string, string>,
): string {
  if (!identity) return "Select the matching chapter";
  const fields = [
    "board", "grade", "subject", "unit", "chapter_title", "chapter_code",
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
    return "This run is already active in another tab or worker. Aegis did not "
      + "start a duplicate.";
  }
  return message;
}
