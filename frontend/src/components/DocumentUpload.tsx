import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useOptionalAuth } from "../Auth";
import { useRunConsole } from "../RunConsole";
import type { UploadJob } from "../types";
import MmdViewer from "./MmdViewer";
import SourceBookInput from "./SourceBookInput";

type Module = "assessments" | "concepts";
type MasterLane = "post" | "pre";

type ActionableArtifact = {
  kind: string;
  label: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  download_url: string;
  action?: "download" | "post" | string;
  disabled?: boolean;
  disabled_reason?: string;
  // Why an ENABLED output will be empty (e.g. the run recorded a
  // Pre-lane refusal or an assumes-nothing verdict). The download stays
  // open; the card explains what's inside before it is opened.
  note?: string;
  requires_confirmation?: boolean;
};

const MASTER_KIND: Record<MasterLane, string> = {
  pre: "pre_release_master",
  post: "release_master",
};

const CONCEPT_KIND: Record<MasterLane, string> = {
  pre: "pre_release_bulk_import",
  post: "release_bulk_import",
};

function masterLane(kind: string): MasterLane | null {
  if (kind === MASTER_KIND.pre) return "pre";
  if (kind === MASTER_KIND.post) return "post";
  return null;
}

function masterIsAvailable(job: UploadJob, lane: MasterLane): boolean {
  const artifact = job.source_artifacts?.files.find(
    (candidate) => candidate.kind === MASTER_KIND[lane],
  );
  return Boolean(artifact && !artifact.disabled && artifact.download_url);
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function errorStatus(error: unknown): number | undefined {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : undefined;
}

function formatMasterRebuildError(
  error: unknown,
  lane: MasterLane,
  durableReason = "",
): string {
  const status = (error as { status?: unknown } | null)?.status;
  const combined = `${readableError(error)} ${durableReason}`;
  const storageFull = status === 507
    || /(?:errno\s*28|enospc|no space left|insufficient storage)/i.test(combined);
  const laneLabel = lane === "pre" ? "Pre-Learning" : "Post-Learning";
  if (storageFull) {
    return "Server storage is full. No " + laneLabel
      + " Master File was published. Ask an operator to restore capacity on "
      + "the server filesystem, then press this Rebuild button again. The "
      + "Concept File is safe; do not rerun concept generation.";
  }
  return `${laneLabel} Master rebuild failed: ${readableError(error)}`;
}

// A recorded reason can run to several sentences; lead with the first
// and fold the rest, exactly like the disabled reasons.
function foldedReason(reason: string) {
  const brief = reason.split(/(?<=\.)\s+/)[0];
  return brief.length < reason.length ? (
    <details>
      <summary>{brief}</summary>
      <div className="mt-8">{reason}</div>
    </details>
  ) : reason;
}

const DRIVE_BACKUP_FOLDER_URL =
  import.meta.env.VITE_CHECKPOINT_DRIVE_FOLDER_URL?.trim() || "";

type SavedJobMarker = {
  id: number;
  module: string;
  learning_kind: string;
  filename: string;
  created_at: string;
};

function safeStorageGetItem(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeStorageSetItem(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Persistence is best-effort (for example, storage may be disabled/full).
  }
}

function safeStorageRemoveItem(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Persistence is best-effort (for example, storage may be disabled).
  }
}

/**
 * Document intake. Uploading a file starts its parse in one go (owner
 * request, 2026-08-28):
 *   1. Choose a file (staged locally — change it freely)
 *   2. Upload → the file is stored AND converted to MMD immediately
 *      (status "converted"); the Console streams the parse live
 *   3. Pick the deposit target, then start generation — or, when the
 *      parent chose every parameter up front, the converted job flows
 *      straight into generation via ``onConverted`` (owner request,
 *      2026-08-29: one action runs the whole chain).
 * Replacing the file stays manual: a replacement is stored without
 * parsing until Convert is pressed, so a wrong pick costs nothing.
 */
export default function DocumentUpload({
  module,
  conceptKind,
  uploadType,
  bookSources = [],
  externalJob,
  disabled = false,
  onJob,
  uploadLabel,
  uploadHint,
  onConverted,
}: {
  module: Module;
  conceptKind?: "post" | "pre";
  uploadType?: string;
  bookSources?: string[];
  externalJob?: UploadJob | null;
  disabled?: boolean;
  onJob: (job: UploadJob | null) => void;
  /** Overrides the Upload button text (one-shot flows name the whole run). */
  uploadLabel?: string;
  /** Overrides the pre-upload hint copy beneath the file row. */
  uploadHint?: string;
  /**
   * Called after a FRESH upload finishes converting (owner request,
   * 2026-08-29: parameters are chosen up front and one action runs the
   * whole chain). Deliberately not called for a manual Convert of a
   * replaced file or for a restored saved run — those keep their
   * explicit, separate steps.
   */
  onConverted?: (job: UploadJob) => void;
}) {
  const { run } = useRunConsole();
  const auth = useOptionalAuth();
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<UploadJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoringSavedJob, setRestoringSavedJob] = useState(false);
  const [savedJobRestoreAttempt, setSavedJobRestoreAttempt] = useState(0);
  const [savedJobRestoreError, setSavedJobRestoreError] = useState<string | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const controlsDisabled = busy || disabled;
  const inputRef = useRef<HTMLInputElement>(null);
  const checkpointInputRef = useRef<HTMLInputElement>(null);
  const savedJobRequestGenerationRef = useRef(0);
  const onJobRef = useRef(onJob);
  useEffect(() => {
    onJobRef.current = onJob;
  }, [onJob]);
  const ownerSuffix = auth?.config?.mode === "google" && auth.user?.sub
    ? `:${encodeURIComponent(auth.user.sub)}`
    : "";
  const storageKey =
    `aegis-upload-job:${module}:${conceptKind ?? uploadType ?? "default"}${ownerSuffix}`;
  const automaticDriveBackup = Boolean(
    auth?.config?.drive_checkpoint_backup?.enabled
    && auth.config.drive_checkpoint_backup.configured,
  );
  const driveStatus = auth?.config?.drive_checkpoint_backup;
  const backupStatusCopy = automaticDriveBackup
    ? driveStatus?.state === "failed"
      ? "Automatic Drive backup is configured, but its latest background "
        + "attempt failed. Your server checkpoint is safe; ask an administrator "
        + "to check the Fly logs."
      : driveStatus?.verified
        ? "Automatic Drive backup has been verified; each completed stage is "
          + "queued in the background after the server save."
        : "Automatic Drive backup is configured and awaiting verification; "
          + "each completed stage is queued after the server save."
    : "Server checkpoints are automatic; Download checkpoint remains an "
      + "optional portable backup.";

  function invalidateSavedJobRestore() {
    savedJobRequestGenerationRef.current += 1;
    setRestoringSavedJob(false);
    setSavedJobRestoreError(null);
  }

  function emit(j: UploadJob | null) {
    invalidateSavedJobRestore();
    setJob(j);
    onJobRef.current(j);
    if (j) {
      safeStorageSetItem(storageKey, JSON.stringify({
        id: j.id,
        module: j.module,
        learning_kind: j.learning_kind,
        filename: j.filename,
        created_at: j.created_at,
      }));
    }
    else safeStorageRemoveItem(storageKey);
  }

  useEffect(() => {
    const raw = safeStorageGetItem(storageKey);
    if (!raw) {
      setSavedJobRestoreError(null);
      return;
    }
    let parsedMarker: unknown;
    try {
      parsedMarker = JSON.parse(raw);
    } catch {
      safeStorageRemoveItem(storageKey);
      return;
    }
    if (!isSavedJobMarker(parsedMarker)) {
      safeStorageRemoveItem(storageKey);
      return;
    }
    const marker = parsedMarker;
    const requestGeneration = savedJobRequestGenerationRef.current + 1;
    savedJobRequestGenerationRef.current = requestGeneration;
    let active = true;
    setSavedJobRestoreError(null);
    setRestoringSavedJob(true);
    api.getUploadJob(module, marker.id)
      .then((saved) => {
        if (
          !active
          || savedJobRequestGenerationRef.current !== requestGeneration
        ) return;
        if (
          saved.module !== marker.module
          || saved.learning_kind !== marker.learning_kind
          || saved.filename !== marker.filename
          || saved.created_at !== marker.created_at
        ) {
          safeStorageRemoveItem(storageKey);
          return;
        }
        setSavedJobRestoreError(null);
        setJob(saved);
        onJobRef.current(saved);
      })
      .catch((restoreError: unknown) => {
        if (
          !active
          || savedJobRequestGenerationRef.current !== requestGeneration
        ) return;
        const status = errorStatus(restoreError);
        // A hard refresh during a deployment, a transient 5xx, an expired
        // session, or a network interruption must not destroy the browser's
        // only pointer to an otherwise durable paid run. Only an authoritative
        // "gone" response proves that this exact saved job cannot be reopened.
        if (status === 404 || status === 410) {
          safeStorageRemoveItem(storageKey);
          setSavedJobRestoreError(
            "This saved run no longer exists on the server.",
          );
          return;
        }
        setSavedJobRestoreError(
          "Could not reopen the saved run: " + readableError(restoreError)
            + ". The saved pointer is still safe; retry after the server or "
            + "sign-in session is available.",
        );
      })
      .finally(() => {
        if (
          active
          && savedJobRequestGenerationRef.current === requestGeneration
        ) setRestoringSavedJob(false);
      });
    return () => {
      active = false;
      if (savedJobRequestGenerationRef.current === requestGeneration) {
        savedJobRequestGenerationRef.current += 1;
      }
    };
  }, [module, savedJobRestoreAttempt, storageKey]);

  useEffect(() => {
    if (module !== "concepts" || !job?.generation_running) return;
    let active = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const fresh = await api.getUploadJob(module, job.id);
        if (!active) return;
        setJob(fresh);
        onJobRef.current(fresh);
        safeStorageSetItem(storageKey, JSON.stringify({
          id: fresh.id,
          module: fresh.module,
          learning_kind: fresh.learning_kind,
          filename: fresh.filename,
          created_at: fresh.created_at,
        }));
        if (fresh.generation_running) {
          timer = window.setTimeout(poll, 3000);
        }
      } catch (pollError) {
        if (!active) return;
        const status = errorStatus(pollError);
        // Keep trying only for transport/server failures. Authentication
        // failures are surfaced by AuthProvider, while a deleted job cannot
        // become available through polling.
        if (status === undefined || status >= 500) {
          timer = window.setTimeout(poll, 3000);
        }
      }
    };

    timer = window.setTimeout(poll, 3000);
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [job?.generation_running, job?.id, module, storageKey]);

  useEffect(() => {
    if (!externalJob) return;
    savedJobRequestGenerationRef.current += 1;
    setRestoringSavedJob(false);
    setJob(externalJob);
    safeStorageSetItem(storageKey, JSON.stringify({
      id: externalJob.id,
      module: externalJob.module,
      learning_kind: externalJob.learning_kind,
      filename: externalJob.filename,
      created_at: externalJob.created_at,
    }));
  }, [externalJob, storageKey]);

  async function upload() {
    if (!file || disabled) return;
    invalidateSavedJobRestore();
    setBusy(true);
    setError(null);
    try {
      const created: UploadJob = module === "assessments"
        ? await api.createAssessmentUpload(uploadType || "document", file, source)
        : await api.postLearningUpload(file, source);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      emit(created);
      // Parsing starts in one go with the upload (owner request,
      // 2026-08-28), and when the parent chose its parameters up front
      // the converted job continues straight into generation through
      // ``onConverted`` (owner request, 2026-08-29). Replacing the file
      // stays manual, so a wrong pick is still free to swap before its
      // replacement is parsed.
      await convertJob(created, { continueRun: true });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function replace(newFile: File) {
    if (!job || disabled) return;
    setBusy(true);
    setError(null);
    try {
      const updated = module === "assessments"
        ? await api.replaceAssessmentFile(job.id, newFile)
        : await api.replaceConceptFile(job.id, newFile);
      emit(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function convertJob(
    target: UploadJob,
    options?: { continueRun?: boolean },
  ) {
    if (disabled) return;
    invalidateSavedJobRestore();
    const requestGeneration = savedJobRequestGenerationRef.current;
    setBusy(true);
    setError(null);
    const path = module === "assessments"
      ? api.paths.assessmentConvert(target.id)
      : api.paths.conceptConvert(target.id);
    try {
      const result = await run<{
        status: string;
        mmd_text: string;
        mmd_chars: number;
        source_artifacts?: UploadJob["source_artifacts"];
        openai_usage?: UploadJob["openai_usage"];
      }>(
        `Converting ${target.filename} to MMD`,
        path,
        {},
        // The parse run reads and extends the SAME cumulative ledger the
        // generation run continues — label it the same way.
        { cumulative: true, filename: target.filename },
        {
          module,
          jobId: target.id,
          // The conversion finished while the connection was down:
          // rebuild the result from the completed job itself.
          recoverResult: async () => {
            const finished = await api.getUploadJob(module, target.id);
            return {
              status: finished.status,
              mmd_text: finished.mmd_text,
              mmd_chars: finished.mmd_text.length,
              source_artifacts: finished.source_artifacts,
              openai_usage: finished.openai_usage,
            };
          },
        },
      );
      if (savedJobRequestGenerationRef.current !== requestGeneration) return;
      const convertedJob: UploadJob = {
        ...target,
        status: "converted",
        mmd_text: result.mmd_text,
        source_artifacts: result.source_artifacts,
        openai_usage: result.openai_usage ?? target.openai_usage,
      };
      emit(convertedJob);
      if (options?.continueRun) onConverted?.(convertedJob);
    } catch (e) {
      if (savedJobRequestGenerationRef.current === requestGeneration) {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  async function convert() {
    if (!job || disabled) return;
    await convertJob(job);
  }

  async function restoreCheckpoint(file: File) {
    if (disabled || module !== "concepts" || !conceptKind) return;
    invalidateSavedJobRestore();
    setBusy(true);
    setError(null);
    try {
      emit(await api.importConceptCheckpoint(file, conceptKind));
    } catch (e) {
      setError(String(e));
    } finally {
      if (checkpointInputRef.current) checkpointInputRef.current.value = "";
      setBusy(false);
    }
  }

  async function clearSavedCheckpoint() {
    if (!job || disabled || module !== "concepts") return;
    setBusy(true);
    setError(null);
    try {
      emit(await api.clearConceptCheckpoint(job.id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function releaseLatestOutput() {
    if (!job || disabled || module !== "concepts") return;
    setBusy(true);
    setError(null);
    try {
      emit(await api.releaseConceptOutput(job.id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    if (disabled) return;
    invalidateSavedJobRestore();
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
    emit(null);
  }

  // Step 1/2 — no job yet: stage + upload.
  if (!job) {
    return (
      <div className="card">
        <SourceBookInput
          value={source}
          onChange={setSource}
          options={bookSources}
          disabled={controlsDisabled}
        />
        <div className="row mt-8">
          <input ref={inputRef} type="file" disabled={controlsDisabled}
            onChange={(e) => {
              invalidateSavedJobRestore();
              setFile(e.target.files?.[0] ?? null);
            }} />
          <button disabled={!file || controlsDisabled} onClick={upload}>
            {busy
              ? <><span className="spinner" aria-hidden="true" /> Uploading…</>
              : uploadLabel || "Upload"}
          </button>
          {file && <span className="muted mono">{file.name}</span>}
        </div>
        <div className="hint mt-8">
          {uploadHint
            || "Uploading stores the file and starts its conversion right "
            + "away — watch the Console for parse progress. You pick where "
            + "to deposit before anything is generated."}
        </div>
        {restoringSavedJob && (
          <div className="muted mt-8" role="status">
            <span className="spinner" aria-hidden="true" /> Checking for a saved run…
          </div>
        )}
        {savedJobRestoreError && (
          <div className="error-box mt-8" role="alert">
            <div>{savedJobRestoreError}</div>
            {safeStorageGetItem(storageKey) && (
              <button
                className="ghost mt-8"
                disabled={restoringSavedJob}
                onClick={() => setSavedJobRestoreAttempt((value) => value + 1)}
              >
                Retry saved run
              </button>
            )}
          </div>
        )}
        {module === "concepts" && (
          <div className="checkpoint-restore">
            <div className="checkpoint-copy">
              <strong>Restore an optional backup</strong>
              <span className="muted">
                Signed-in runs are checkpointed automatically on this server and
                offered when you open Build Concepts.
              </span>
              <span className="muted">
                {backupStatusCopy} Import a JSON file here only when restoring
                a portable backup.
              </span>
            </div>
            <label
              className="upload-label"
              style={{ opacity: controlsDisabled ? 0.5 : 1 }}
            >
              Import checkpoint backup
              <input
                ref={checkpointInputRef}
                type="file"
                accept=".json,.aegis-checkpoint.json,application/json"
                disabled={controlsDisabled}
                style={{ display: "none" }}
                onChange={(e) => {
                  const selected = e.target.files?.[0];
                  if (selected) void restoreCheckpoint(selected);
                }}
              />
            </label>
            {DRIVE_BACKUP_FOLDER_URL && (
              <a
                className="button-link ghost"
                href={DRIVE_BACKUP_FOLDER_URL}
                target="_blank"
                rel="noreferrer"
              >
                Open Google Drive backup folder
              </a>
            )}
          </div>
        )}
        {error && <div className="error-box mt-8">{error}</div>}
      </div>
    );
  }

  const generated = job.status === "generated";
  const released = job.status === "released";
  const converted = (
    job.status === "converted"
    || generated
    || released
    || Boolean(job.mmd_text)
    || job.checkpoint_available
  );

  // Step 3 — uploaded (and maybe converted).
  return (
    <div className="card">
      <div className="row">
        <span className={`badge ${converted ? "green" : "accent"}`}>
          {generated
            ? "uploaded to database"
            : released
              ? "output released for review"
              : converted
                ? "converted to MMD"
                : "uploaded (not processed)"}
        </span>
        <span className="muted mono">{job.filename}</span>
        {job.source_book && <span className="badge accent">{job.source_book}</span>}
        <div className="spacer" />
        {!generated && !released && (
          <label
            className="upload-label"
            style={{ opacity: controlsDisabled ? 0.5 : 1 }}
          >
            Replace file
            <input
              type="file"
              disabled={controlsDisabled}
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && replace(e.target.files[0])} />
          </label>
        )}
        <button
          className="ghost"
          disabled={controlsDisabled}
          onClick={reset}
          title={
            job.checkpoint_available
              ? "Leave this run in the saved-runs list without deleting its checkpoint"
              : "Clear this upload from this browser"
          }
        >
          {job.checkpoint_available ? "Keep for later" : "Start over"}
        </button>
      </div>

      {!converted && (
        <div className="row mt-12">
          <button disabled={controlsDisabled} onClick={convert}>
            Convert to MMD
          </button>
          <span className="hint">
            Runs conversion/normalization — watch the Console for progress.
          </span>
        </div>
      )}

      {converted && job.mmd_text && (
        <MmdViewer text={job.mmd_text} filename={job.filename} />
      )}
      {converted && (
        <SourceArtifactsCard
          actionsDisabled={controlsDisabled}
          manifest={job.source_artifacts}
          jobId={job.id}
          jobRunning={Boolean(job.generation_running)}
          onPublished={(freshJob) => emit(freshJob)}
        />
      )}
      {module === "concepts" && converted && (
        <div className={`checkpoint-card ${
          job.checkpoint_available ? "checkpoint-ready" : ""
        }`}>
          <div>
            <strong>
              {released
                ? "Released output is ready"
                : job.checkpoint_available
                  ? `Saved checkpoint at ${Math.round(
                    (job.checkpoint_progress ?? 0) * 100,
                  )}%`
                  : "Portable converted-source backup"}
            </strong>
            <div className="muted">
              {released
                ? "Download the workbook or full diagnostic context. Database publication is a separate explicit action."
                : job.checkpoint_available
                  ? `Stage: ${formatCheckpointStage(
                    job.checkpoint_stage,
                  )}. The next run resumes automatically.`
                  : "Download this to preserve the converted MMD across deployments."}
            </div>
            {job.checkpoint_target_identity
              && Object.keys(job.checkpoint_target_identity).length > 0 && (
              <div className="muted checkpoint-target">
                Target: {formatCheckpointTarget(job.checkpoint_target_identity)}
              </div>
            )}
            <div className="muted">
              {backupStatusCopy}
              {auth?.config?.drive_checkpoint_backup?.notice
                ? ` ${auth.config.drive_checkpoint_backup.notice}`
                : ""}
            </div>
          </div>
          <div className="row">
            {!released && !generated && (
              <button
                className="ghost"
                disabled={controlsDisabled}
                onClick={releaseLatestOutput}
              >
                Release latest output
              </button>
            )}
            <a
              className="button-link ghost"
              href={api.checkpointUrl(job.id)}
              download
            >
              Download checkpoint
            </a>
            <a
              className="button-link ghost"
              href={api.runDiagnosticsUrl(job.id)}
              download
              title={"Everything this run saved — every checkpoint stage, "
                + "the full generation log, and per-stage artifacts — in "
                + "one shareable zip, whether or not the run finished."}
            >
              Download run diagnostics
            </a>
            {DRIVE_BACKUP_FOLDER_URL && (
              <a
                className="button-link ghost"
                href={DRIVE_BACKUP_FOLDER_URL}
                target="_blank"
                rel="noreferrer"
              >
                Open Google Drive backup folder
              </a>
            )}
            {job.checkpoint_available && !released && !generated && (
              <button
                className="danger"
                disabled={controlsDisabled}
                onClick={clearSavedCheckpoint}
              >
                Discard checkpoint
              </button>
            )}
          </div>
          <PersistedDiagnostics job={job} />
        </div>
      )}
      {error && <div className="error-box mt-8">{error}</div>}
    </div>
  );
}

function SourceArtifactsCard({
  actionsDisabled,
  manifest,
  jobId,
  jobRunning,
  onPublished,
}: {
  actionsDisabled: boolean;
  manifest?: UploadJob["source_artifacts"];
  jobId: number;
  jobRunning: boolean;
  onPublished: (job: UploadJob) => void;
}) {
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [rebuildingMaster, setRebuildingMaster] = useState<
    Record<MasterLane, boolean>
  >({ pre: false, post: false });
  const [masterErrors, setMasterErrors] = useState<
    Partial<Record<MasterLane, string>>
  >({});
  const [masterNotices, setMasterNotices] = useState<
    Partial<Record<MasterLane, string>>
  >({});
  if (!manifest?.available) return null;
  const anyMasterRebuilding = rebuildingMaster.pre || rebuildingMaster.post;
  const summary = manifest.summary ?? {};
  const phase2 = manifest.generation_usage?.mode === "source-critical";
  const adjudication = manifest.source_adjudication;
  const adjudicationStatus = adjudication?.status ?? "";
  const reconstruction = manifest.source_reconstruction;
  const reconstructionVerified = reconstruction?.status === "verified"
    && reconstruction?.source_origin === "gpt_pdf_acsd_fallback";
  const reconstructionReviewRequired = reconstruction?.status === "review_required"
    && reconstruction?.source_origin === "gpt_pdf_acsd_fallback";
  const phase22 = phase2 && ["pending", "verified", "review_required"].includes(
    adjudicationStatus,
  );
  const pendingAdjudication = phase22 && adjudicationStatus === "pending";
  const statusClass = manifest.status === "passed"
    || (phase2 && manifest.phase2_inventory_ready) ? "green" : "accent";
  const statusLabel = reconstructionReviewRequired
    ? "source reconstruction review required"
    : pendingAdjudication
      ? "awaiting source adjudication"
      : manifest.status.replace(/_/g, " ");
  const title = reconstructionVerified
    ? "Phase 2.2.1 GPT-reconstructed canonical source"
    : reconstructionReviewRequired
      ? "Phase 2.2.1 GPT source reconstruction review"
      : phase22
      ? (adjudicationStatus === "verified"
        ? "Phase 2.2.1 canonical-source inventory"
        : "Phase 2.2.1 canonical-source review")
      : phase2
        ? "Phase 2 canonical-source inventory"
        : "Phase 1 canonical-source shadow";
  const usageBadge = phase2
    ? "source-critical generation active"
    : "not used for generation";
  const adjudicationBadge = adjudicationStatus === "verified"
    ? "source adjudication verified"
    : adjudicationStatus === "pending"
      ? "AI source adjudication pending"
      : adjudicationStatus === "review_required"
        ? "source review required"
        : "";
  const reconstructionBadge = reconstructionVerified
    ? "GPT PDF-to-ACSD"
    : reconstructionReviewRequired
      ? "GPT PDF-to-ACSD review required"
      : "";
  const description = reconstructionVerified
    ? `Aegis read ${
      reconstruction?.page_count ?? 0
    } PDF page(s) into strict page/block JSON, independently verified the batches, `
      + "materialized source-owned visual crops, and compiled the result through the "
      + "same deterministic ACSD gates. The original PDF remains the audit authority."
    : reconstructionReviewRequired
      ? "GPT PDF-to-ACSD could not verify a complete reading of this PDF. The "
        + "original PDF and its audit artifacts remain preserved, and concept "
        + "generation is blocked."
      : phase22 && adjudicationStatus === "pending"
      ? "The deterministic ACSD gate found bounded source gaps. Generate will inspect "
        + "only the relevant original-document pages, accept verbatim visible evidence, "
        + "and rerun every source contract before concept extraction."
      : phase22 && adjudicationStatus === "verified"
        ? "Build Concepts uses the deterministic ACSD ledger plus verified, auditable "
          + "source-visible overlays recovered from the original document. The immutable "
          + "raw MMD remains available unchanged."
        : phase2
          ? "Build Concepts now reads task order, stable QIDs, Figure ownership, images, "
            + "KaTeX, and inventory identity from ACSD. Semantic concept extraction and "
            + "writing still read the immutable raw MMD."
          : "The current pipeline still reads the immutable raw MMD. These files let "
            + "you inspect source order, task boundaries, images, KaTeX, and validation "
            + "before any future cutover.";
  const counts = [
    ["sections", summary.sections],
    ["blocks", summary.blocks],
    ["tasks", summary.tasks],
    ["images", summary.images],
    ["math spans", summary.math_spans],
  ]
    .filter((entry): entry is [string, number] => Number.isFinite(entry[1]))
    .map(([label, value]) => `${value} ${label}`)
    .join(" · ");

  function artifactLane(artifact: ActionableArtifact): "post" | "pre" {
    // WHICH LANE THIS CONTROL PUBLISHES, read off the entry it was
    // rendered from. One job stages two releases (Outputs 01/02 and
    // Outputs 03/04); the lane rides the entry's own download_url,
    // exactly as the four download links do.
    return new URLSearchParams(
      artifact.download_url.split("?")[1] ?? "",
    ).get("lane") === "pre" ? "pre" : "post";
  }

  // Owner steer 2026-08-20: the way into the CMS is the reviewer's own
  // file — download the Concept workbook, edit it locally in Excel, and
  // upload it back. The upload is applied as one recorded review round
  // and published in the same act; the separate "upload to database"
  // button is gone with it.
  async function uploadEditedWorkbook(
    artifact: ActionableArtifact, file: File,
  ) {
    if (
      actionsDisabled || actionBusy || anyMasterRebuilding || artifact.disabled
    ) return;
    const lane = artifactLane(artifact);
    const laneLabel = lane === "pre" ? "Pre-Learning" : "Post-Learning";
    if (
      !window.confirm(
        `Apply ${file.name} as ${laneLabel} edits and upload to the CMS `
        + "now? Your changes are recorded as a review round first.",
      )
    ) return;
    setActionBusy(true);
    setActionMessage(null);
    try {
      const summary = await api.uploadEditedWorkbook(jobId, lane, file);
      const fresh = await api.getUploadJob("concepts", jobId);
      onPublished(fresh);
      const changed = Number(summary["changed_fields"] ?? 0);
      setActionMessage(
        `${laneLabel}: ${file.name} applied`
        + (changed > 0
          ? ` (${changed} field change${changed === 1 ? "" : "s"} recorded)`
          : " (no field changes)")
        + " and uploaded to the CMS.",
      );
    } catch (e) {
      setActionMessage(String(e));
    } finally {
      setActionBusy(false);
    }
  }

  // The owner's four outputs (OD4 numbering) get the first-class grid; the
  // publish actions follow them; every evidence artifact — real, but not a
  // deliverable — lives in the disclosure below.
  const files = manifest.files.map((raw) => raw as ActionableArtifact);
  const outputs = files.filter((f) => f.kind in OUTPUT_META);
  const publishActions = files.filter((f) => f.action === "post");
  const evidence = files.filter(
    (f) => !(f.kind in OUTPUT_META) && f.action !== "post",
  );

  function sameLaneConceptIsAvailable(lane: MasterLane): boolean {
    const concept = outputs.find(
      (artifact) => artifact.kind === CONCEPT_KIND[lane],
    );
    return Boolean(concept && !concept.disabled && concept.download_url);
  }

  async function rebuildMasterLane(lane: MasterLane) {
    // The backend serializes explicit Master rebuilds for one job. Keep the
    // sibling control visible but disabled so a second lane cannot predictably
    // collide with the active rebuild and return 409.
    if (
      actionsDisabled || actionBusy || jobRunning || anyMasterRebuilding
    ) return;
    const laneLabel = lane === "pre" ? "Pre-Learning" : "Post-Learning";
    setRebuildingMaster((current) => ({ ...current, [lane]: true }));
    setMasterErrors((current) => ({ ...current, [lane]: undefined }));
    setMasterNotices((current) => ({ ...current, [lane]: undefined }));

    let requestError: unknown = null;
    let refreshError: unknown = null;
    let fresh: UploadJob | null = null;
    try {
      try {
        await api.rebuildMasterFromConceptJob(jobId, lane);
      } catch (error) {
        requestError = error;
      }

      // Refresh after both success and failure. A response can be lost after
      // the server committed the release, while a failed request may have
      // recorded a more precise durable reason on the job manifest.
      try {
        fresh = await api.getUploadJob("concepts", jobId);
        onPublished(fresh);
      } catch (error) {
        refreshError = error;
      }

      if (fresh && masterIsAvailable(fresh, lane)) {
        setMasterNotices((current) => ({
          ...current,
          [lane]: `${laneLabel} Master File rebuilt. Download is ready.`,
        }));
        return;
      }

      if (requestError) {
        const durableReason = fresh?.source_artifacts?.files.find(
          (artifact) => artifact.kind === MASTER_KIND[lane],
        )?.disabled_reason ?? "";
        setMasterErrors((current) => ({
          ...current,
          [lane]: formatMasterRebuildError(
            requestError, lane, durableReason,
          ),
        }));
        return;
      }

      if (refreshError) {
        setMasterErrors((current) => ({
          ...current,
          [lane]: `${laneLabel} Master File was rebuilt, but the output cards `
            + `could not refresh: ${readableError(refreshError)}. Reload this `
            + "page; do not rerun concept generation.",
        }));
        return;
      }

      setMasterErrors((current) => ({
        ...current,
        [lane]: `${laneLabel} Master rebuild completed, but the server did `
          + "not expose a downloadable file. Reload the page before trying again.",
      }));
    } finally {
      setRebuildingMaster((current) => ({ ...current, [lane]: false }));
    }
  }

  return (
    <div className="checkpoint-card">
      <div>
        <div className="row">
          <strong>{title}</strong>
          <span className={`badge ${statusClass}`}>
            {statusLabel}
          </span>
          <span className={`badge ${phase2 ? "green" : "accent"}`}>
            {usageBadge}
          </span>
          {reconstructionBadge && (
            <span className={`badge ${reconstructionVerified ? "green" : "accent"}`}>
              {reconstructionBadge}
            </span>
          )}
          {adjudicationBadge && (
            <span className={`badge ${adjudicationStatus === "verified" ? "green" : "accent"}`}>
              {adjudicationBadge}
            </span>
          )}
        </div>
        {counts && <div className="muted mono mt-8">{counts}</div>}
        {actionMessage && (
          <div className="muted mt-8" role="status">
            {actionMessage}
          </div>
        )}
      </div>

      {outputs.length > 0 && (
        <div>
          <div className="section-title">Run outputs</div>
          <div className="outputs-grid">
            {outputs.map((artifact) => {
              const meta = OUTPUT_META[artifact.kind];
              const lane = masterLane(artifact.kind);
              const canRebuild = Boolean(
                lane
                && artifact.disabled
                && sameLaneConceptIsAvailable(lane),
              );
              const laneBusy = lane ? rebuildingMaster[lane] : false;
              return (
                <div
                  className={`output-card${artifact.disabled ? " output-disabled" : ""}`}
                  key={artifact.kind}
                >
                  <div className="output-eyebrow">
                    <span>{meta.num}</span>
                    <span className={`badge ${meta.pre ? "accent" : "green"}`}>
                      {meta.lane}
                    </span>
                  </div>
                  <div className="output-name">{meta.name}</div>
                  {artifact.disabled ? (
                    <>
                      <div className="output-reason">
                        {foldedReason(
                          artifact.disabled_reason
                            || "Not available for this run.",
                        )}
                      </div>
                      {canRebuild && lane && (
                        <>
                          <div className="muted mt-8" role="note">
                            Uses the already-generated {meta.lane} Concept File.
                            It does not regenerate or modify Concepts.
                          </div>
                          <button
                            aria-label={`Rebuild ${meta.lane} Master File`}
                            className="ghost output-rebuild"
                            disabled={
                              actionsDisabled
                              || actionBusy
                              || jobRunning
                              || anyMasterRebuilding
                            }
                            onClick={() => void rebuildMasterLane(lane)}
                            title={jobRunning
                              ? "Wait for the active generation run to finish."
                              : `Rebuild only the ${meta.lane} Master File from its preserved Concept File.`}
                          >
                            {laneBusy
                              ? <><span className="spinner" aria-hidden="true" /> Rebuilding…</>
                              : "Rebuild Master"}
                          </button>
                        </>
                      )}
                    </>
                  ) : (
                    <>
                      {artifact.note && (
                        <div className="output-reason" role="note">
                          {foldedReason(artifact.note)}
                        </div>
                      )}
                      {artifact.size_bytes > 0 && (
                        <div className="output-meta">
                          {formatBytes(artifact.size_bytes)}
                        </div>
                      )}
                      <a
                        className="button-link"
                        href={artifact.download_url}
                        download={artifact.filename || undefined}
                        aria-label={artifact.label}
                      >
                        Download
                      </a>
                    </>
                  )}
                  {lane && masterNotices[lane] && (
                    <div className="muted" role="status">
                      {masterNotices[lane]}
                    </div>
                  )}
                  {lane && masterErrors[lane] && (
                    <div className="error-box output-action-error" role="alert">
                      {masterErrors[lane]}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {publishActions.length > 0 && (
        <div className="row">
          {publishActions.map((artifact) => {
            const lane = artifactLane(artifact);
            const laneLabel = lane === "pre" ? "Pre-Learning" : "Post-Learning";
            const inputId = `edited-workbook-${jobId}-${lane}`;
            return (
              <span key={artifact.kind}>
                <input
                  accept=".xlsx"
                  data-testid={`upload-edited-${lane}`}
                  disabled={
                    actionsDisabled
                    || actionBusy
                    || anyMasterRebuilding
                    || artifact.disabled
                  }
                  id={inputId}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) void uploadEditedWorkbook(artifact, file);
                  }}
                  style={{ display: "none" }}
                  type="file"
                />
                <button
                  className="ghost"
                  disabled={
                    actionsDisabled
                    || actionBusy
                    || anyMasterRebuilding
                    || artifact.disabled
                  }
                  onClick={() =>
                    document.getElementById(inputId)?.click()}
                >
                  {actionBusy
                    ? <><span className="spinner" aria-hidden="true" /> Uploading…</>
                    : `Upload edited ${laneLabel} Excel to CMS`}
                </button>
              </span>
            );
          })}
        </div>
      )}

      <details className="artifact-evidence">
        <summary>Source pipeline details &amp; evidence files</summary>
        <div className="muted mt-8">
          {description}
        </div>
        <div className="muted mono mt-8">
          ACSD {manifest.schema_version} · compiler {manifest.compiler_version}
          {manifest.phase ? ` · ${manifest.phase.replace(/-/g, " ")}` : ""}
        </div>
        <div className="row">
          {evidence.map((artifact) => (
            <a
              className="button-link ghost"
              href={artifact.download_url}
              download={artifact.filename || undefined}
              key={artifact.kind}
              title={artifact.size_bytes > 0
                ? `${artifact.label} · ${formatBytes(artifact.size_bytes)}`
                : artifact.label}
            >
              {artifact.label}
            </a>
          ))}
        </div>
      </details>
    </div>
  );
}

/* The owner's output numbering (OD4 / D9-Q22). Kind strings deliberately
   carry no digits (T14) — the numbers live here, at the presentation. */
const OUTPUT_META: Record<
  string,
  { num: string; lane: string; name: string; pre: boolean }
> = {
  pre_release_bulk_import: {
    num: "01", lane: "Pre-Learning", name: "Concept File", pre: true,
  },
  pre_release_master: {
    num: "02", lane: "Pre-Learning", name: "Master File", pre: true,
  },
  release_bulk_import: {
    num: "03", lane: "Post-Learning", name: "Concept File", pre: false,
  },
  release_master: {
    num: "04", lane: "Post-Learning", name: "Master File", pre: false,
  },
};

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "unknown size";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatCheckpointStage(stage?: string): string {
  return (stage || "saved stage").replace(/_/g, " ");
}

function isSavedJobMarker(value: unknown): value is SavedJobMarker {
  if (!value || typeof value !== "object") return false;
  const marker = value as Partial<SavedJobMarker>;
  return (
    Number.isInteger(marker.id)
    && Number(marker.id) > 0
    && typeof marker.module === "string"
    && typeof marker.learning_kind === "string"
    && typeof marker.filename === "string"
    && typeof marker.created_at === "string"
  );
}

function formatCheckpointTarget(identity: Record<string, string>): string {
  const orderedFields = [
    "board",
    "grade",
    "subject",
    "unit",
    "chapter_title",
    "chapter_code",
  ];
  const values = orderedFields
    .map((field) => identity[field]?.trim())
    .filter((value): value is string => Boolean(value));
  return values.length ? values.join(" / ") : "saved destination";
}

function PersistedDiagnostics({ job }: { job: UploadJob }) {
  const diagnostics = (job.generation_log ?? [])
    .filter((event) =>
      event.type === "log"
      && ["error", "warn", "warning"].includes(event.level ?? ""))
    .slice(-8);
  if (!diagnostics.length && !job.detail.startsWith("Generation failed:")) {
    return null;
  }
  return (
    <details className="checkpoint-diagnostics">
      <summary>Last saved error details</summary>
      {diagnostics.map((event, index) => (
        <div className="mono" key={`${event.ts ?? 0}-${index}`}>
          {event.message}
        </div>
      ))}
      {!diagnostics.length && <div className="mono">{job.detail}</div>}
    </details>
  );
}
