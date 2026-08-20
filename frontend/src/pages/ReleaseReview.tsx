import { useCallback, useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { RichDetails } from "../lib/richText";
import type {
  ReleaseReviewConcept,
  ReleaseReviewEdit,
  ReleaseReviewEditField,
  ReleaseReviewLane,
  ReleaseReviewView,
} from "../types";

/**
 * Step 9 — the review/edit surface for one staged release lane.
 *
 * Deep-linked (not a nav tab): /build-concepts/review/:jobId?lane=post|pre.
 * The reviewer reads the staged rows, fixes a field in place, or describes a
 * change in plain language for the model to apply. Publication stays on the
 * Build Concepts page (Rule G — a separate, explicit, authenticated act);
 * this page only links there.
 *
 * Every write carries the staged_release_uid the view was read from. A 409
 * means someone else moved the release underneath us — we say so and ask for
 * a reload instead of silently clobbering their version.
 */

const STATE_BADGE: Record<ReleaseReviewView["state"], string> = {
  ready: "green",
  ready_with_flags: "yellow",
  diagnostic_release: "red",
};

const STALE_MESSAGE =
  "This release changed under you; reload to pick up the latest version, then re-apply your change.";

/**
 * The http helper folds the status into the thrown message (the body detail
 * when there is one, "409 Conflict" otherwise), so both shapes are checked.
 * This is transport mechanics, not a content judgment.
 */
function isStaleConflict(message: string): boolean {
  return message.includes("409") || /stale/i.test(message);
}

type EditDraft = Record<ReleaseReviewEditField, string>;

const EDITABLE_FIELDS: {
  field: ReleaseReviewEditField;
  label: string;
  multiline: boolean;
}[] = [
  { field: "topic", label: "Topic", multiline: false },
  { field: "parent_concept", label: "Parent concept", multiline: false },
  { field: "concept_title", label: "Concept title", multiline: false },
  { field: "concept_details", label: "Concept details", multiline: true },
  { field: "keywords", label: "Keywords", multiline: false },
];

function ConceptEditForm({
  recordIndex,
  original,
  busy,
  error,
  onSave,
  onCancel,
}: {
  recordIndex: number;
  original: EditDraft;
  busy: boolean;
  error: string;
  onSave: (edits: ReleaseReviewEdit[]) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<EditDraft>(original);

  function save() {
    // Only the fields that changed travel, each with its before value so the
    // server can refuse an edit built on a row it no longer holds.
    const edits: ReleaseReviewEdit[] = [];
    for (const { field } of EDITABLE_FIELDS) {
      if (draft[field] !== original[field]) {
        edits.push({
          record_index: recordIndex,
          field,
          before: original[field],
          after: draft[field],
        });
      }
    }
    if (edits.length === 0) {
      onCancel(); // nothing changed — no round-trip
      return;
    }
    onSave(edits);
  }

  return (
    <div className="concept-edit-form">
      {EDITABLE_FIELDS.map(({ field, label, multiline }) => (
        <label key={field} className="concept-edit-field">
          <span>{label}</span>
          {multiline ? (
            <textarea
              data-testid={`edit-field-${field}`}
              rows={5}
              value={draft[field]}
              disabled={busy}
              onChange={(event) =>
                setDraft({ ...draft, [field]: event.target.value })
              }
            />
          ) : (
            <input
              data-testid={`edit-field-${field}`}
              value={draft[field]}
              disabled={busy}
              onChange={(event) =>
                setDraft({ ...draft, [field]: event.target.value })
              }
            />
          )}
        </label>
      ))}
      <div className="row">
        <button
          className="primary"
          data-testid="save-edit"
          onClick={save}
          disabled={busy}
        >
          {busy ? (
            <>
              <span className="spinner" aria-hidden="true" /> Saving…
            </>
          ) : (
            "Save"
          )}
        </button>
        <button className="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
      {error && (
        <div className="error-box mt-8" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

export default function ReleaseReview() {
  const { jobId } = useParams<{ jobId: string }>();
  const [searchParams] = useSearchParams();
  const lane: ReleaseReviewLane =
    searchParams.get("lane") === "pre" ? "pre" : "post";
  const jobIdNum = Number(jobId);

  const [view, setView] = useState<ReleaseReviewView | null>(null);
  const [loadError, setLoadError] = useState("");

  const [instruction, setInstruction] = useState("");
  const [applying, setApplying] = useState(false);
  const [instructionError, setInstructionError] = useState("");

  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState("");

  const load = useCallback(async () => {
    setLoadError("");
    try {
      setView(await api.getReleaseReview(jobIdNum, lane));
      setEditingIndex(null);
      setEditError("");
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, [jobIdNum, lane]);

  useEffect(() => {
    if (!Number.isFinite(jobIdNum)) return;
    void load();
  }, [jobIdNum, load]);

  async function applyInstruction() {
    const text = instruction.trim();
    if (!text || !view || applying) return;
    setApplying(true);
    setInstructionError("");
    try {
      const updated = await api.applyReleaseInstruction(jobIdNum, {
        lane,
        staged_release_uid: view.staged_release_uid,
        instruction: text,
      });
      setView(updated);
      setInstruction("");
    } catch (e) {
      // A failed model round (502) surfaces its detail and keeps the box, so
      // the reviewer can retry without retyping.
      setInstructionError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(false);
    }
  }

  async function saveEdits(edits: ReleaseReviewEdit[]) {
    if (!view || editBusy) return;
    setEditBusy(true);
    setEditError("");
    try {
      const updated = await api.submitReleaseManualEdit(jobIdNum, {
        lane,
        staged_release_uid: view.staged_release_uid,
        edits,
      });
      setView(updated);
      setEditingIndex(null);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setEditError(isStaleConflict(message) ? STALE_MESSAGE : message);
    } finally {
      setEditBusy(false);
    }
  }

  if (!Number.isFinite(jobIdNum)) {
    return (
      <>
        <h1>Release review</h1>
        <div className="error-box">Missing or invalid job id in the URL.</div>
      </>
    );
  }

  return (
    <>
      <h1>Release review — job {jobIdNum}</h1>
      <div className="subtitle">
        Staged {lane === "post" ? "post-learning" : "pre-learning"} release.
        Fix a row in place, or describe a change in plain language. Publishing
        stays on the Build Concepts page:{" "}
        <Link to="/build-concepts">Open Output workbooks &amp; publish</Link>
      </div>

      {loadError && (
        <div className="error-box mb-16" role="alert">
          {loadError}{" "}
          <button className="ghost" onClick={() => void load()}>
            Reload
          </button>
        </div>
      )}
      {!view && !loadError && (
        <div className="row muted">
          <span className="spinner" aria-hidden="true" />
          <span>Loading…</span>
        </div>
      )}

      {view && (
        <>
          <div className="card">
            <div className="row">
              <span className={`badge ${STATE_BADGE[view.state] ?? ""}`}>
                {view.state}
              </span>
              <span className="muted" data-testid="review-summary">
                {view.summary.row_count} rows · {view.summary.issue_count}{" "}
                issues ({view.summary.error_count} errors,{" "}
                {view.summary.warning_count} warnings) ·{" "}
                {view.summary.database_uploaded
                  ? "uploaded to database"
                  : "not uploaded to database"}{" "}
                · staged v{view.staged_version}{" "}
                <span className="mono">{view.staged_release_uid}</span>
              </span>
              <div className="spacer" />
              <button className="ghost" onClick={() => void load()}>
                Reload
              </button>
            </div>
          </div>

          <div className="section-title">Describe a change</div>
          <div className="card">
            <p className="muted">
              Aegis applies your instruction to this staged release and shows
              the result here as a new version. Nothing is published by this.
            </p>
            <textarea
              className="textarea-block"
              data-testid="instruction-box"
              aria-label="What needs changing?"
              rows={4}
              value={instruction}
              disabled={applying}
              placeholder={
                "e.g. Move “How d governs Sn” under Sum of First n Terms, " +
                "and shorten the keywords on every concept in §5.2."
              }
              onChange={(event) => setInstruction(event.target.value)}
            />
            <div className="row mt-12">
              <button
                className="primary"
                data-testid="apply-instruction"
                onClick={() => void applyInstruction()}
                disabled={applying || !instruction.trim()}
              >
                {applying ? (
                  <>
                    <span className="spinner" aria-hidden="true" /> Applying…
                  </>
                ) : (
                  "Apply changes"
                )}
              </button>
            </div>
            {instructionError && (
              <div className="error-box mt-12" role="alert">
                {instructionError}
              </div>
            )}
          </div>

          {view.issues.length > 0 && (
            <>
              <div className="section-title">Issues</div>
              <div className="card">
                <ul className="stack" data-testid="issues-list">
                  {view.issues.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>
                      <span
                        className={`badge ${
                          issue.severity === "error" ? "red" : "yellow"
                        }`}
                      >
                        {issue.severity}
                      </span>{" "}
                      <span className="mono">{issue.code}</span> —{" "}
                      {issue.message}
                    </li>
                  ))}
                </ul>
              </div>
            </>
          )}

          {view.topics.map((topicGroup) => (
            <section key={topicGroup.topic} data-testid="review-topic">
              <div className="section-title">{topicGroup.topic}</div>
              <div className="stack">
                {topicGroup.concepts.map((concept: ReleaseReviewConcept) => (
                  <div
                    className="card"
                    data-testid="review-concept"
                    key={concept.record_index}
                  >
                    {editingIndex === concept.record_index ? (
                      <ConceptEditForm
                        recordIndex={concept.record_index}
                        original={{
                          topic: topicGroup.topic,
                          parent_concept: concept.parent_concept,
                          concept_title: concept.concept_title,
                          concept_details: concept.concept_details,
                          keywords: concept.keywords,
                        }}
                        busy={editBusy}
                        error={editError}
                        onSave={(edits) => void saveEdits(edits)}
                        onCancel={() => {
                          setEditingIndex(null);
                          setEditError("");
                        }}
                      />
                    ) : (
                      <>
                        <div className="row">
                          <strong>{concept.concept_title}</strong>
                          {concept.release_status && (
                            <span className="badge">
                              {concept.release_status}
                            </span>
                          )}
                          <div className="spacer" />
                          <button
                            className="ghost"
                            data-testid="edit-concept"
                            onClick={() => {
                              setEditingIndex(concept.record_index);
                              setEditError("");
                            }}
                          >
                            Edit
                          </button>
                        </div>
                        {concept.review_flags.length > 0 && (
                          <div className="row mt-8">
                            {concept.review_flags.map((flag) => (
                              <span key={flag} className="badge yellow">
                                {flag}
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="muted mt-8">
                          Parent concept: {concept.parent_concept}
                        </div>
                        <RichDetails details={concept.concept_details} />
                        <div className="muted">
                          Keywords: {concept.keywords}
                        </div>
                        {concept.release_errors.length > 0 && (
                          <div className="error-box mt-8">
                            {concept.release_errors.join("\n")}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ))}

          <div className="section-title">Version history</div>
          <div className="card">
            {view.versions.length === 0 && (
              <p className="empty">
                No review rounds yet — the history starts with your first
                edit or instruction.
              </p>
            )}
            <ol className="stack" data-testid="version-history">
              {view.versions.map((version) => (
                <li key={version.version}>
                  <span className="mono">v{version.version}</span>{" "}
                  <span className="badge">{version.origin}</span>
                  {version.change_count > 0 &&
                    ` (${version.change_count} change${
                      version.change_count === 1 ? "" : "s"
                    })`}{" "}
                  · <span className="muted">{version.created_at}</span>
                  {version.instruction && (
                    <div className="muted">{version.instruction}</div>
                  )}
                </li>
              ))}
            </ol>
          </div>
        </>
      )}
    </>
  );
}
