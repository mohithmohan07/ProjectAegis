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
    <div className="card" style={{ marginTop: 8 }}>
      <div className="muted" style={{ marginBottom: 8 }}>
        Upload your syllabus structure workbooks (CBSE, ICSE, Maharashtra, Karnataka,
        English Language). This fills the directory so you can <strong>manually pick</strong>{" "}
        where to deposit concepts — nothing is inferred from the PDF filename.
      </div>
      <label className="upload-label">
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
      {msg && <div className="muted mono" style={{ marginTop: 8 }}>{msg}</div>}
      {error && <div className="error-box" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  );
}
