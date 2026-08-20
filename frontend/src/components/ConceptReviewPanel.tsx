import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { ConceptRevision } from "../types";

/**
 * Post-run review: download the workbook, say what needs correcting, repeat.
 *
 * Generation never stops to ask a question. It ships an output, flagging any
 * placement it made by its own best reading rather than a certified one. This
 * panel is where a reviewer corrects that afterwards, in plain language, as
 * many times as it takes -- there is deliberately no round limit.
 *
 * The instruction history lives in the database, never in the workbook, so the
 * delivered file only ever carries corrected content.
 */
export function ConceptReviewPanel({ jobId }: { jobId: number }) {
  const [instruction, setInstruction] = useState("");
  const [revisions, setRevisions] = useState<ConceptRevision[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const listed = await api.listConceptRevisions(jobId);
      setRevisions(listed.revisions);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [jobId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submit() {
    const text = instruction.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.submitConceptRevision(jobId, text);
      // Keep the box only if the round failed, so the reviewer can retry
      // without retyping. A recorded round always survives on the server.
      setInstruction("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const latest = revisions[revisions.length - 1];
  const openFlags = latest?.flagged_placements ?? [];

  return (
    <div className="card mt-16">
      <div className="section-title">Review and correct the output</div>
      <p className="muted">
        The files are in the Run outputs section above. Read them, then
        describe anything that needs changing — move a concept to another
        topic, reword it, or add one that is missing. Aegis applies your
        instruction and rebuilds the files, as many times as you need.
      </p>

      <p>
        <Link
          data-testid="open-review-page"
          to={`/build-concepts/review/${jobId}?lane=post`}
        >
          Open the review & edit page (Post-Learning)
        </Link>
        {" · "}
        <Link
          data-testid="open-review-page-pre"
          to={`/build-concepts/review/${jobId}?lane=pre`}
        >
          Pre-Learning
        </Link>
      </p>

      {openFlags.length > 0 && (
        <div className="mono mt-12" data-testid="flagged-placements">
          <strong>Aegis placed these by its own best reading:</strong>
          <ul>
            {openFlags.map((flag, index) => (
              <li key={index}>{flag.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="field mt-16">
        <label className="field-label" htmlFor="revision-instruction">
          What needs changing?
        </label>
        <textarea
          className="textarea-block"
          id="revision-instruction"
          rows={5}
          value={instruction}
          placeholder={
            "e.g. Move “How d governs Sn” under Sum of First n Terms — it needs " +
            "the sum formula, so the earlier topic is only a prerequisite. And " +
            "add a concept under §5.2 on recognising a real-world sequence as " +
            "an AP; it is missing."
          }
          onChange={(event) => setInstruction(event.target.value)}
          disabled={busy}
        />
      </div>
      <div className="row">
        <button
          className="primary"
          onClick={() => void submit()}
          disabled={busy || !instruction.trim()}
        >
          {busy && <><span className="spinner" aria-hidden="true" />{" "}</>}
          {busy ? "Applying…" : "Apply changes"}
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {revisions.length > 0 && (
        <div data-testid="revision-history">
          <div className="section-title">Revision history</div>
          <ol>
            {revisions.map((revision) => (
              <li key={revision.id} className="mb-12">
                <div className="mono muted">
                  Round {revision.round_number} — {revision.status}
                  {revision.status === "applied" &&
                    ` (${revision.change_count} change${
                      revision.change_count === 1 ? "" : "s"
                    })`}
                </div>
                <div>{revision.instruction}</div>
                {revision.change_summary && (
                  <div className="muted">{revision.change_summary}</div>
                )}
                {revision.error && (
                  <div className="error">{revision.error}</div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
