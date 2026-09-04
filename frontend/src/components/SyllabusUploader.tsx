import { useState } from "react";
import { api } from "../api/client";

/** Upload syllabus Excel files to populate Board → Class → Subject → Unit → Chapter. */
export default function SyllabusUploader({
  onLoaded,
  disabled = false,
}: {
  onLoaded?: () => void;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setMsg(null);
    setError(null);
    try {
      const result = await api.uploadSyllabus([...files]);
      setMsg(
        `Loaded ${String(result.created ?? 0)} chapters from `
        + `${(result.uploaded_files as string[] | undefined)?.length ?? files.length} file(s).`,
      );
      onLoaded?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="syllabus-uploader">
      <summary>Chapter not listed? Upload syllabus structure workbooks</summary>
      <div className="hint mt-8">
        Syllabus workbooks (CBSE, ICSE, Maharashtra, Karnataka, English
        Language) fill the directory so you can pick where to deposit
        concepts — nothing is inferred from the PDF filename.
      </div>
      <label className="upload-label upload-label-ghost mt-8">
        {busy && <span className="spinner" aria-hidden="true" />}
        Upload syllabus Excel files
        <input
          type="file"
          accept=".xlsx"
          multiple
          disabled={disabled || busy}
          style={{ display: "none" }}
          onChange={(e) => upload(e.target.files)}
        />
      </label>
      {msg && <div className="muted mono mt-8">{msg}</div>}
      {error && <div className="error-box mt-8">{error}</div>}
    </details>
  );
}
