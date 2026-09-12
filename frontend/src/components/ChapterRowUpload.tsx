import { useRef, useState } from "react";
import { api } from "../api/client";
import type { ChapterBatchRow } from "../types";

export type ChapterUploadSlot = "source" | "concept" | "master";

const ACCEPT: Record<ChapterUploadSlot, string> = {
  source: ".pdf,.md,.mmd,.txt",
  concept: ".xlsx",
  master: ".xlsx",
};

const VERB: Record<ChapterUploadSlot, string> = {
  source: "Upload source",
  concept: "Upload reviewed Concept file",
  master: "Upload reviewed Master file",
};

/**
 * A thin per-row file input.
 *
 * Deliberately NOT `DocumentUpload`: that component derives one
 * localStorage key per (module, kind, owner) with no row scoping
 * (`DocumentUpload.tsx:290-291`), so N of them on one page share one saved
 * job. It also never calls `useRunConsole()` — the console holds a single
 * RunState and one monotonic run id, so the last row to mount captures it
 * and detaches the others (contract §11).
 *
 * This component therefore owns exactly two pieces of state, its own
 * pending flag and its own error, touches no browser storage, and scopes
 * every DOM id by chapter id.
 */
export default function ChapterRowUpload({
  chapterId,
  slot,
  lane,
  disabled = false,
  onUploaded,
  sourceBook = "",
  chapterDurationMinutes = 0,
  label,
  compact = false,
}: {
  chapterId: number;
  slot: ChapterUploadSlot;
  lane?: string;
  disabled?: boolean;
  onUploaded: (row: ChapterBatchRow) => void;
  /** Source slot only: recorded on the batch row with the staged file. */
  sourceBook?: string;
  chapterDurationMinutes?: number;
  label?: string;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const inputId = `chapter-${chapterId}-upload-${slot}${lane ? `-${lane}` : ""}`;
  const errorId = `${inputId}-error`;

  async function send(file: File) {
    setBusy(true);
    setError(null);
    try {
      let row: ChapterBatchRow;
      if (slot === "source") {
        row = await api.chapterBatchStageSource(
          chapterId,
          file,
          sourceBook,
          chapterDurationMinutes,
        );
      } else if (slot === "concept") {
        row = await api.chapterBatchUploadConceptReview(
          chapterId,
          lane ?? "post",
          file,
        );
      } else {
        row = await api.chapterBatchUploadMasterReview(
          chapterId,
          lane ?? "post",
          file,
        );
      }
      onUploaded(row);
    } catch (e) {
      // Publication ordering answers 409 with a readable detail
      // ("publish the Concept file first"). Show it verbatim.
      setError(String(e));
    } finally {
      setBusy(false);
      // Let the same file be chosen again after a failure.
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div
      className={compact ? "chapter-upload is-compact" : "chapter-upload"}
      data-testid={`${inputId}-wrap`}
    >
      <label
        className={`upload-label upload-label-ghost${disabled || busy ? " is-disabled" : ""}`}
        htmlFor={inputId}
      >
        {busy ? "Uploading…" : label ?? VERB[slot]}
        <input
          id={inputId}
          ref={inputRef}
          type="file"
          accept={ACCEPT[slot]}
          disabled={disabled || busy}
          style={{ display: "none" }}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file);
          }}
        />
      </label>
      {error && (
        <div className="error-box mt-8" id={errorId} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
