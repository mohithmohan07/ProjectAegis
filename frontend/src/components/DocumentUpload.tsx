import { useRef, useState } from "react";
import { api } from "../api/client";
import { useRunConsole } from "../RunConsole";
import type { UploadJob } from "../types";
import SourceBookInput from "./SourceBookInput";

type Module = "assessments" | "concepts";

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
  onJob,
}: {
  module: Module;
  conceptKind?: "post" | "pre";
  uploadType?: string;
  bookSources?: string[];
  onJob: (job: UploadJob | null) => void;
}) {
  const { run } = useRunConsole();
  const [source, setSource] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<UploadJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function emit(j: UploadJob | null) {
    setJob(j);
    onJob(j);
  }

  async function upload() {
    if (!file) return;
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

  function reset() {
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
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button disabled={!file || busy} onClick={upload}>
            {busy ? "Uploading…" : "Upload"}
          </button>
          {file && <span className="muted mono">{file.name}</span>}
        </div>
        <div className="muted" style={{ marginTop: 8 }}>
          Uploading only stores the file — it is <strong>not</strong> processed yet, so you
          can swap it if you picked the wrong one. Convert to MMD as a separate step.
        </div>
        {error && <div className="error-box" style={{ marginTop: 8 }}>{error}</div>}
      </div>
    );
  }

  const converted = job.status === "converted";

  // Step 3 — uploaded (and maybe converted).
  return (
    <div className="card">
      <div className="row">
        <span className={`badge ${converted ? "green" : "accent"}`}>
          {converted ? "converted to MMD" : "uploaded (not processed)"}
        </span>
        <span className="muted mono">{job.filename}</span>
        {job.source_book && <span className="badge accent">{job.source_book}</span>}
        <div className="spacer" />
        <label className="upload-label" style={{ opacity: busy ? 0.5 : 1 }}>
          Replace file
          <input type="file" disabled={busy} style={{ display: "none" }}
            onChange={(e) => e.target.files?.[0] && replace(e.target.files[0])} />
        </label>
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
      {error && <div className="error-box" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  );
}
