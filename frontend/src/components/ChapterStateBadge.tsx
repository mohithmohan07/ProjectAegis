import type { ChapterBatchPage, ChapterBatchRow } from "../types";
import {
  attemptLabel,
  badgeClass,
  queuePositionLabel,
  stateLabel,
  stateTone,
} from "../lib/chapterBatchState";

/**
 * One row's state, rendered from the server's vocabulary.
 *
 * The label comes from `row.state_label`, falling back to the response's
 * `states[]` list and only then to the local table — the client never
 * invents a state name (contract §10/§11). A queued row also shows its
 * place in the queue, so "queued" is not indistinguishable from "stuck".
 */
export default function ChapterStateBadge({
  row,
  states,
}: {
  row: ChapterBatchRow;
  states?: ChapterBatchPage["states"];
}) {
  const tone = stateTone(row.state, states);
  const label = stateLabel(row.state, row.state_label, states);
  const position = queuePositionLabel(row);
  const attempt = attemptLabel(row);
  return (
    <span className="chapter-state" data-state={row.state} data-tone={tone}>
      <span
        className={badgeClass(tone)}
        data-testid={`chapter-${row.chapter_id}-state`}
      >
        {label}
      </span>
      {position && (
        <span
          className="hint chapter-state-note"
          data-testid={`chapter-${row.chapter_id}-queue-position`}
        >
          {position}
        </span>
      )}
      {attempt && (
        <span className="hint chapter-state-note">{attempt}</span>
      )}
    </span>
  );
}
