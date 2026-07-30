import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useOptionalAuth } from "../Auth";
import { useRunConsole } from "../RunConsole";
import type { UploadJob } from "../types";
import SourceBookInput from "./SourceBookInput";

type Module = "assessments" | "concepts";

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
 * Three-step document intake that NEVER auto-processes:
 *   1. Choose a file (staged locally — change it freely)
 *   2. Upload  → file is stored on the server (status "uploaded"), no MMD yet
 *   3. Convert → explicit, streamed MMD conversion (status "converted")
 * Re-upload/replace is allowed any time before generation.
 */
export default function DocumentUpload({
  module,
  conceptKind,
  uploadType,
  bookSources = [],
  externalJob,
  disabled = false,
  onJob,
}: {
  module: Module;
  conceptKind?: "post" | "pre";
  uploadType?: string;
  bookSources?: string[];
  externalJob?: UploadJob | null;
  disabled?: boolean;
  onJob: (job: UploadJob | null) => void;
}) {
  const { run } = useRunConsole();
  const auth = useOptionalAuth();
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<UploadJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoringSavedJob, setRestoringSavedJob] = useState(false);
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
    if (!raw) return;
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
        setJob(saved);
        onJobRef.current(saved);
      })
      .catch(() => {
        if (
          active
          && savedJobRequestGenerationRef.current === requestGeneration
        ) safeStorageRemoveItem(storageKey);
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
  }, [module, storageKey]);

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
      let created: UploadJob;
      if (module === "assessments") {
        created = await api.createAssessmentUpload(uploadType || "document", file, source);
      } else if (conceptKind === "pre") {
        created = await api.preLearningUpload(file, source);
      } else {
        created = await api.postLearningUpload(file, source);
      }
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      emit(created);
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

  async function convert() {
    if (!job || disabled) return;
    invalidateSavedJobRestore();
    const requestGeneration = savedJobRequestGenerationRef.current;
    setBusy(true);
    setError(null);
    const path = module === "assessments"
      ? api.paths.assessmentConvert(job.id)
      : api.paths.conceptConvert(job.id);
    try {
      const result = await run<{
        status: string;
        mmd_text: string;
        mmd_chars: number;
        source_artifacts?: UploadJob["source_artifacts"];
        openai_usage?: UploadJob["openai_usage"];
      }>(`Converting ${job.filename} to MMD`, path);
      if (savedJobRequestGenerationRef.current !== requestGeneration) return;
      emit({
        ...job,
        status: "converted",
        mmd_text: result.mmd_text,
        source_artifacts: result.source_artifacts,
        openai_usage: result.openai_usage ?? job.openai_usage,
      });
    } catch (e) {
      if (savedJobRequestGenerationRef.current === requestGeneration) {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
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
        <div className="row" style={{ marginTop: 8 }}>
          <input ref={inputRef} type="file" disabled={controlsDisabled}
            onChange={(e) => {
              invalidateSavedJobRestore();
              setFile(e.target.files?.[0] ?? null);
            }} />
          <button disabled={!file || controlsDisabled} onClick={upload}>
            {busy ? "Uploading…" : "Upload"}
          </button>
          {file && <span className="muted mono">{file.name}</span>}
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          Uploading only stores the file — it is <strong>not</strong> processed yet, so you
          can swap it if you picked the wrong one. Convert to MMD as a separate step.
        </div>
        {restoringSavedJob && (
          <div className="muted" role="status" style={{ marginTop: 8 }}>
            Checking for a saved run…
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
        {error && <div className="error-box" style={{ marginTop: 8 }}>{error}</div>}
      </div>
    );
  }

  const generated = job.status === "generated";
  const converted = job.status === "converted" || generated;

  // Step 3 — uploaded (and maybe converted).
  return (
    <div className="card">
      <div className="row">
        <span className={`badge ${converted ? "green" : "accent"}`}>
          {generated
            ? "generation complete"
            : converted
              ? "converted to MMD"
              : "uploaded (not processed)"}
        </span>
        <span className="muted mono">{job.filename}</span>
        {job.source_book && <span className="badge accent">{job.source_book}</span>}
        <div className="spacer" />
        {!generated && (
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
        <div className="row" style={{ marginTop: 10 }}>
          <button disabled={controlsDisabled} onClick={convert}>
            Convert to MMD
          </button>
          <span className="muted">Runs Mathpix/normalization — watch the Console for progress.</span>
        </div>
      )}

      {converted && job.mmd_text && (
        <pre className="mmd-preview">{job.mmd_text.slice(0, 800)}</pre>
      )}
      {converted && <SourceArtifactsCard manifest={job.source_artifacts} />}
      {module === "concepts" && converted && (
        <div className={`checkpoint-card ${
          job.checkpoint_available ? "checkpoint-ready" : ""
        }`}>
          <div>
            <strong>
              {job.checkpoint_available
                ? `Saved checkpoint at ${Math.round(
                  (job.checkpoint_progress ?? 0) * 100,
                )}%`
                : "Portable converted-source backup"}
            </strong>
            <div className="muted">
              {job.checkpoint_available
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
            <a
              className="button-link ghost"
              href={api.checkpointUrl(job.id)}
              download
            >
              Download checkpoint
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
            {job.checkpoint_available && (
              <button
                className="ghost"
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
      {error && <div className="error-box" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  );
}

function SourceArtifactsCard({
  manifest,
}: {
  manifest?: UploadJob["source_artifacts"];
}) {
  if (!manifest?.available) return null;
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
    ? "GPT PDF-to-ACSD fallback"
    : reconstructionReviewRequired
      ? "GPT PDF-to-ACSD review required"
      : "";
  const description = reconstructionVerified
    ? `Mathpix was unavailable or objectively unusable, so Aegis extracted ${
      reconstruction?.page_count ?? 0
    } PDF page(s) into strict page/block JSON, independently verified the batches, `
      + "materialized source-owned visual crops, and compiled the result through the "
      + "same deterministic ACSD gates. The original PDF remains the audit authority."
    : reconstructionReviewRequired
      ? "Mathpix failed the objective source-quality gate, and GPT PDF-to-ACSD "
        + "could not verify a complete replacement. The original PDF and Mathpix "
        + "audit artifacts remain preserved, and concept generation is blocked."
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

  return (
    <div className="checkpoint-card" style={{ marginTop: 10 }}>
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
        <div className="muted" style={{ marginTop: 6 }}>
          {description}
        </div>
        {counts && <div className="muted mono" style={{ marginTop: 6 }}>{counts}</div>}
        <div className="muted mono" style={{ marginTop: 4 }}>
          ACSD {manifest.schema_version} · compiler {manifest.compiler_version}
          {manifest.phase ? ` · ${manifest.phase.replace(/-/g, " ")}` : ""}
        </div>
      </div>
      <div className="row">
        {manifest.files.map((artifact) => (
          <a
            className="button-link ghost"
            href={artifact.download_url}
            download={artifact.filename}
            key={artifact.kind}
            title={`${artifact.label} · ${formatBytes(artifact.size_bytes)}`}
          >
            {artifact.label}
          </a>
        ))}
      </div>
    </div>
  );
}

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
