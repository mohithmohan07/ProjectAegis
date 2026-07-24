import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useRunConsole } from "../RunConsole";
import type { UploadJob } from "../types";
import SourceBookInput from "./SourceBookInput";

type Module = "assessments" | "concepts";

const DEFAULT_DRIVE_BACKUP_FOLDER_URL =
  "https://drive.google.com/drive/folders/1ZrgyXqB339m312XqhxLWMu5Z5H15Ggyo";
const DRIVE_BACKUP_FOLDER_URL =
  import.meta.env.VITE_CHECKPOINT_DRIVE_FOLDER_URL?.trim()
  || DEFAULT_DRIVE_BACKUP_FOLDER_URL;

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
  onJob,
}: {
  module: Module;
  conceptKind?: "post" | "pre";
  uploadType?: string;
  bookSources?: string[];
  externalJob?: UploadJob | null;
  onJob: (job: UploadJob | null) => void;
}) {
  const { run } = useRunConsole();
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<UploadJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoringSavedJob, setRestoringSavedJob] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const checkpointInputRef = useRef<HTMLInputElement>(null);
  const savedJobRequestGenerationRef = useRef(0);
  const storageKey =
    `aegis-upload-job:${module}:${conceptKind ?? uploadType ?? "default"}`;

  function invalidateSavedJobRestore() {
    savedJobRequestGenerationRef.current += 1;
    setRestoringSavedJob(false);
  }

  function emit(j: UploadJob | null) {
    invalidateSavedJobRestore();
    setJob(j);
    onJob(j);
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
        onJob(saved);
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
  }, [module, onJob, storageKey]);

  useEffect(() => {
    if (externalJob && externalJob.id === job?.id) {
      setJob(externalJob);
    }
  }, [externalJob, job?.id]);

  async function upload() {
    if (!file) return;
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
    if (!job) return;
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
    if (!job) return;
    setError(null);
    const path = module === "assessments"
      ? api.paths.assessmentConvert(job.id)
      : api.paths.conceptConvert(job.id);
    try {
      const result = await run<{ status: string; mmd_text: string; mmd_chars: number }>(
        `Converting ${job.filename} to MMD`, path);
      emit({ ...job, status: "converted", mmd_text: result.mmd_text });
    } catch (e) {
      setError(String(e));
    }
  }

  async function restoreCheckpoint(file: File) {
    if (module !== "concepts" || !conceptKind) return;
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
    if (!job || module !== "concepts") return;
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
    invalidateSavedJobRestore();
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
    emit(null);
  }

  // Step 1/2 — no job yet: stage + upload.
  if (!job) {
    return (
      <div className="card">
        <SourceBookInput value={source} onChange={setSource} options={bookSources} disabled={busy} />
        <div className="row" style={{ marginTop: 8 }}>
          <input ref={inputRef} type="file" disabled={busy}
            onChange={(e) => {
              invalidateSavedJobRestore();
              setFile(e.target.files?.[0] ?? null);
            }} />
          <button disabled={!file || busy} onClick={upload}>
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
              <strong>Continue a saved run</strong>
              <span className="muted">
                Restore an Aegis checkpoint file from this computer or Google Drive.
              </span>
              <span className="muted">
                Back up: download a checkpoint and upload it to Drive. Resume:
                download the JSON from Drive, then choose Restore checkpoint.
                Aegis does not sync with Drive automatically.
              </span>
            </div>
            <label className="upload-label" style={{ opacity: busy ? 0.5 : 1 }}>
              Restore checkpoint
              <input
                ref={checkpointInputRef}
                type="file"
                accept=".json,.aegis-checkpoint.json,application/json"
                disabled={busy}
                style={{ display: "none" }}
                onChange={(e) => {
                  const selected = e.target.files?.[0];
                  if (selected) void restoreCheckpoint(selected);
                }}
              />
            </label>
            <a
              className="button-link ghost"
              href={DRIVE_BACKUP_FOLDER_URL}
              target="_blank"
              rel="noreferrer"
            >
              Open Google Drive backup folder
            </a>
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
          <label className="upload-label" style={{ opacity: busy ? 0.5 : 1 }}>
            Replace file
            <input type="file" disabled={busy} style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && replace(e.target.files[0])} />
          </label>
        )}
        <button className="ghost" disabled={busy} onClick={reset}>Start over</button>
      </div>

      {!converted && (
        <div className="row" style={{ marginTop: 10 }}>
          <button disabled={busy} onClick={convert}>Convert to MMD</button>
          <span className="muted">Runs Mathpix/normalization — watch the Console for progress.</span>
        </div>
      )}

      {converted && job.mmd_text && (
        <pre className="mmd-preview">{job.mmd_text.slice(0, 800)}</pre>
      )}
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
            {job.checkpoint_target_identity && (
              <div className="muted checkpoint-target">
                Target: {formatCheckpointTarget(job.checkpoint_target_identity)}
              </div>
            )}
            <div className="muted">
              Back up: download this file and upload it to Drive. Resume:
              download the JSON from Drive, then choose Restore checkpoint.
              Aegis does not sync with Drive automatically.
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
            <a
              className="button-link ghost"
              href={DRIVE_BACKUP_FOLDER_URL}
              target="_blank"
              rel="noreferrer"
            >
              Open Google Drive backup folder
            </a>
            {job.checkpoint_available && (
              <button
                className="ghost"
                disabled={busy}
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
