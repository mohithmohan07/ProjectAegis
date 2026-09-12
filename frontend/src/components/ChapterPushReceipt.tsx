import type { ChapterBatchPushResult } from "../types";
import { chapterLabel, summarisePush } from "../lib/chapterBatchState";

const VERDICT_LABEL: Record<string, string> = {
  queued: "Queued",
  already_queued: "Already queued",
  already_running: "Already running",
  refused: "Refused",
};

const VERDICT_TONE: Record<string, string> = {
  queued: "badge green",
  already_queued: "badge",
  already_running: "badge accent",
  refused: "badge yellow",
};

/**
 * The per-row verdicts of one push, rendered once.
 *
 * Push, cancel and retry always answer 200 with a per-row verdict — one
 * ineligible row never fails the batch (contract §9) — so a push of twenty
 * rows can return twenty different answers. They belong in one receipt
 * block with the server's own reason sentence, not in twenty alerts.
 */
export default function ChapterPushReceipt({
  result,
  onDismiss,
}: {
  result: ChapterBatchPushResult;
  onDismiss?: () => void;
}) {
  const summary = summarisePush(result);
  return (
    <div className="card chapter-receipt" data-testid="chapter-push-receipt">
      <div className="row">
        <strong data-testid="chapter-push-receipt-line">{summary.sentence}</strong>
        <div className="spacer" />
        {onDismiss && (
          <button className="ghost" onClick={onDismiss}>
            Dismiss
          </button>
        )}
      </div>
      <ul className="stack mt-8">
        {result.results.map((outcome) => (
          <li
            key={outcome.chapter_id}
            className="chapter-receipt-row"
            data-testid={`chapter-${outcome.chapter_id}-verdict`}
            data-verdict={outcome.verdict}
          >
            <span className={VERDICT_TONE[outcome.verdict] ?? "badge"}>
              {VERDICT_LABEL[outcome.verdict] ?? outcome.verdict}
            </span>{" "}
            <span>{chapterLabel(outcome.row)}</span>
            {outcome.position !== null && outcome.position !== undefined && (
              <span className="hint">{` · #${outcome.position} in queue`}</span>
            )}
            {outcome.reason && (
              // The server's sentence, verbatim — including the readable
              // 409 detail behind a publication-order refusal.
              <div className="hint chapter-receipt-reason">{outcome.reason}</div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
