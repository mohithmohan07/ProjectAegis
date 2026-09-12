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

/** The page-level datalist of known publications; see `ChapterBatch`. */
export const BOOK_SOURCES_LIST_ID = "chapter-book-sources";

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
 * This component therefore owns only its own pending flag, its own error
 * and (for a source) the two fields that ride the file, touches no browser
 * storage, and scopes every DOM id by chapter id AND by `scope` — a row and
 * its open drawer both render the same lane's upload, and two elements
 * sharing one id silently mis-wire `htmlFor`.
 *
 * The source slot stages in TWO steps on purpose. `source_book` is the
 * publication that becomes the Concept Source and the extracted
 * Post-Learning Question Source (Q42/Q45); staging with it blank leaves the
 * run with no publication and blocks its database upload later. A file
 * chooser that fires the request the instant a file is picked has nowhere
 * to ask for it, so picking the file only arms the form.
 */
export default function ChapterRowUpload({
  chapterId,
  slot,
  lane,
  scope = "",
  disabled = false,
  onUploaded,
  sourceBook = "",
  chapterDurationMinutes = 0,
  bookSources = [],
  label,
  compact = false,
}: {
  chapterId: number;
  slot: ChapterUploadSlot;
  lane?: string;
  /** Distinguishes two mounts of the same slot+lane (the row and its drawer). */
  scope?: string;
  disabled?: boolean;
  onUploaded: (row: ChapterBatchRow) => void;
  /** Source slot only: recorded on the batch row with the staged file. */
  sourceBook?: string;
  chapterDurationMinutes?: number;
  /** Source slot only: the known publications, for the suggestion list. */
  bookSources?: string[];
  label?: string;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [staged, setStaged] = useState<File | null>(null);
  const [book, setBook] = useState(sourceBook);
  const [minutes, setMinutes] = useState(
    chapterDurationMinutes > 0 ? String(chapterDurationMinutes) : "",
  );
  const inputRef = useRef<HTMLInputElement | null>(null);

  const inputId = `chapter-${chapterId}-${scope ? `${scope}-` : ""}upload-${slot}${
    lane ? `-${lane}` : ""
  }`;
  const errorId = `${inputId}-error`;

  function clearInput() {
    if (inputRef.current) inputRef.current.value = "";
  }

  async function send(file: File) {
    setBusy(true);
    setError(null);
    try {
      let row: ChapterBatchRow;
      if (slot === "source") {
        const parsed = Number.parseInt(minutes, 10);
        row = await api.chapterBatchStageSource(
          chapterId,
          file,
          book.trim(),
          Number.isFinite(parsed) && parsed > 0 ? parsed : 0,
        );
        setStaged(null);
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
      clearInput();
    }
  }

  const chooseLabel = slot === "source"
    ? (staged ? "Choose a different file" : label ?? VERB[slot])
    : label ?? VERB[slot];

  return (
    <div
      className={compact ? "chapter-upload is-compact" : "chapter-upload"}
      data-testid={`${inputId}-wrap`}
    >
      <label
        className={`upload-label upload-label-ghost${disabled || busy ? " is-disabled" : ""}`}
        htmlFor={inputId}
      >
        {busy ? "Uploading…" : chooseLabel}
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
            if (!file) return;
            // A reviewed file carries everything it needs; a source needs
            // its publication named before it is staged.
            if (slot === "source") setStaged(file);
            else void send(file);
          }}
        />
      </label>

      {slot === "source" && staged && (
        <div className="chapter-stage-form" data-testid={`${inputId}-form`}>
          <div className="hint">{staged.name}</div>
          <div className="field">
            <label className="field-label" htmlFor={`${inputId}-book`}>
              Source (publication)
            </label>
            <input
              id={`${inputId}-book`}
              className="input-md"
              list={BOOK_SOURCES_LIST_ID}
              value={book}
              disabled={disabled || busy}
              placeholder="e.g. NCERT, Balbharati…"
              onChange={(e) => setBook(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor={`${inputId}-minutes`}>
              Chapter duration (minutes)
            </label>
            <input
              id={`${inputId}-minutes`}
              className="input-num"
              type="number"
              min={1}
              step={1}
              inputMode="numeric"
              value={minutes}
              disabled={disabled || busy}
              placeholder="optional"
              onChange={(e) => setMinutes(e.target.value)}
            />
          </div>
          <div className="hint">
            The publication becomes this run's Concept Source and the
            extracted Post-Learning Question Source; generated Pre-Learning
            questions use UpSchool DB. Leave it blank and the database upload
            is blocked until one is supplied.
          </div>
          <div className="row">
            <button
              type="button"
              id={`${inputId}-submit`}
              disabled={disabled || busy}
              onClick={() => void send(staged)}
            >
              {busy ? "Staging…" : "Stage source"}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy}
              onClick={() => {
                setStaged(null);
                setError(null);
                clearInput();
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="error-box mt-8" id={errorId} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
