import { useRef, useState } from "react";
import type { ReviewErrorReportReceipt } from "../types";

/** File selection is separate from submission so a review can include notes. */
export default function ReviewedFileUpload({
  inputId, label, accept, disabled, busy, inputTestId, buttonTestId, onUpload,
}: {
  inputId: string;
  label: string;
  accept: string;
  disabled?: boolean;
  busy?: boolean;
  inputTestId?: string;
  buttonTestId?: string;
  onUpload: (file: File, reviewErrorNotes?: string) => Promise<boolean>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [logErrors, setLogErrors] = useState(false);
  const [notes, setNotes] = useState("");

  async function submit() {
    if (!file || disabled || busy) return;
    if (await onUpload(file, logErrors ? notes : undefined)) {
      setFile(null);
      setLogErrors(false);
      setNotes("");
    }
  }

  return <div className="reviewed-file-upload">
    <input
      id={inputId}
      ref={input}
      type="file"
      accept={accept}
      disabled={disabled || busy}
      data-testid={inputTestId}
      style={{ display: "none" }}
      onChange={(event) => {
        const selected = event.target.files?.[0];
        if (selected) setFile(selected);
        event.target.value = "";
      }}
    />
    <button
      className="ghost"
      type="button"
      disabled={disabled || busy}
      data-testid={buttonTestId}
      onClick={() => input.current?.click()}
    >{busy ? "Uploading…" : file ? "Choose a different file" : label}</button>
    {file && <div className="chapter-stage-form" data-testid={`${inputId}-form`}>
      <div className="hint">Selected file: <strong>{file.name}</strong></div>
      <label htmlFor={`${inputId}-log-errors`}>
        <input
          id={`${inputId}-log-errors`}
          type="checkbox"
          checked={logErrors}
          disabled={disabled || busy}
          onChange={(event) => setLogErrors(event.target.checked)}
        />{" "}Log errors for maintenance
      </label>
      {logErrors && <div className="field">
        <label className="field-label" htmlFor={`${inputId}-error-notes`}>
          What did you correct? (optional)
        </label>
        <textarea
          id={`${inputId}-error-notes`}
          className="textarea-block"
          rows={4}
          value={notes}
          disabled={disabled || busy}
          placeholder="Describe the errors you found and the changes you made."
          onChange={(event) => setNotes(event.target.value)}
        />
        <div className="hint mt-4">
          Aegis will include the source, generated outputs, corrected file and
          run history for review during scheduled maintenance.
        </div>
      </div>}
      <div className="row">
        <button
          id={`${inputId}-submit`}
          data-testid={`${inputId}-submit`}
          type="button"
          disabled={disabled || busy}
          onClick={() => void submit()}
        >{busy ? "Uploading…" : "Upload reviewed file"}</button>
        <button
          className="ghost"
          type="button"
          disabled={busy}
          onClick={() => setFile(null)}
        >Cancel</button>
      </div>
    </div>}
  </div>;
}

export function reviewErrorReceipt(value: unknown): ReviewErrorReportReceipt | undefined {
  if (!value || typeof value !== "object") return undefined;
  const receipt = value as ReviewErrorReportReceipt;
  return typeof receipt.report_id === "string" ? receipt : undefined;
}

export function ReviewErrorReceipt({ receipt }: { receipt?: ReviewErrorReportReceipt }) {
  if (!receipt) return null;
  if (receipt.status === "attention_required") return <div className="error-box mt-8" role="alert">
    {receipt.message || "Your upload was accepted, but its error log needs attention. The review evidence has been retained."}
    {" "}Reference: <span className="mono">{receipt.report_id}</span>
  </div>;
  return <div className="hint mt-8" role="status">
    {receipt.review_kind === "master" ? "Master" : "Concept"} error log saved
    {receipt.status === "queued" ? " for scheduled maintenance" : ""}.
    {" "}Reference: <span className="mono">{receipt.report_id}</span>
  </div>;
}
