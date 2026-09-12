import type { ChapterBatchRow } from "../types";
import { publishPlan } from "../lib/chapterBatchState";

/**
 * The one page-level publish confirmation.
 *
 * It replaces `window.confirm` entirely: a publish writes to the database
 * and appends to the shared CMS output workbook, and the person approving
 * it must see exactly which chapters and exactly how many writes — counted
 * per chapter from that chapter's own available lanes, so a Post-only run
 * is confirmed as one write and one append, not two (contract §10).
 */
export default function ChapterPublishDialog({
  rows,
  busy = false,
  onConfirm,
  onCancel,
}: {
  rows: ChapterBatchRow[];
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const plan = publishPlan(rows);
  const nothingToDo = plan.chapters === 0;
  return (
    <div
      className="resume-dialog-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        className="card resume-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="chapter-publish-dialog-title"
        data-testid="chapter-publish-dialog"
      >
        <h2 id="chapter-publish-dialog-title">Publish to the database</h2>
        <p>
          Publication is ordered per lane: each lane's Concept release is
          published first, then its reviewed Master, to the database and the
          shared CMS output workbook. This cannot be undone from this page.
        </p>

        <dl className="kv">
          <div>
            <dt>Chapters</dt>
            <dd data-testid="publish-plan-chapters">{plan.chapters}</dd>
          </div>
          <div>
            <dt>Lanes</dt>
            <dd data-testid="publish-plan-lanes">{plan.lanes}</dd>
          </div>
          <div>
            <dt>Database writes</dt>
            <dd data-testid="publish-plan-writes">{plan.database_writes}</dd>
          </div>
          <div>
            <dt>CMS workbook appends</dt>
            <dd data-testid="publish-plan-appends">{plan.cms_appends}</dd>
          </div>
        </dl>

        {plan.entries.length > 0 && (
          <ul className="stack chapter-publish-list">
            {plan.entries.map((entry) => (
              <li key={entry.chapter_id} data-testid={`publish-plan-${entry.chapter_id}`}>
                <strong>{entry.label}</strong>
                <span className="hint">
                  {` — ${entry.lanes.join(", ")} · ${entry.database_writes} database write`}
                  {entry.database_writes === 1 ? "" : "s"}
                  {` · ${entry.cms_appends} CMS append`}
                  {entry.cms_appends === 1 ? "" : "s"}
                </span>
              </li>
            ))}
          </ul>
        )}

        {plan.skipped.length > 0 && (
          <div className="hint" data-testid="publish-plan-skipped">
            Not sent:{" "}
            {plan.skipped
              .map((skip) => `${skip.label} (${skip.reason})`)
              .join("; ")}
            .
          </div>
        )}

        {nothingToDo && (
          <div className="hint">
            Nothing in this selection has a lane left to publish.
          </div>
        )}

        <div className="row resume-actions">
          <button className="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy || nothingToDo}
            data-testid="chapter-publish-confirm"
          >
            {busy
              ? "Publishing…"
              : `Publish ${plan.chapters} chapter${plan.chapters === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
