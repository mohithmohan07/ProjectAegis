import { currentWorkflowStep } from "../lib/workflowSteps";
import type { ChapterBatchRow } from "../types";
import { stateFor } from "./WorkflowStepStrip";

const STEPS: Array<{ step: 1 | 2 | 3; number: string; title: string }> = [
  { step: 1, number: "01", title: "Generate Concept Files" },
  { step: 2, number: "02", title: "Review Concept files & generate Masters" },
  { step: 3, number: "03", title: "Review Master files & publish" },
];

/**
 * The compact 1/2/3 indicator for a table row.
 *
 * Where a run is in the three-step workflow is read off the same durable
 * Concept-review marker the single-chapter header reads, through the same
 * `currentWorkflowStep` + `stateFor` pair exported from
 * `WorkflowStepStrip` — so the table and that page cannot disagree
 * (contract §11). Nothing here is derived from the queue: a queued Step 02
 * is still "Step 02 current", not "Step 01 complete and nothing else".
 */
export default function ChapterStepPips({ row }: { row: ChapterBatchRow }) {
  const current = currentWorkflowStep({
    review_workflow: row.workflow_status ? { status: row.workflow_status } : null,
  });
  return (
    <ol
      className="chapter-pips"
      aria-label={`Three-step progress for ${row.chapter_code || row.chapter_id}`}
      data-testid={`chapter-${row.chapter_id}-pips`}
    >
      {STEPS.map(({ step, number, title }) => {
        const state = stateFor(step, current);
        return (
          <li
            key={step}
            className={`chapter-pip is-${state}`}
            data-state={state}
            data-testid={`chapter-${row.chapter_id}-pip-${number}`}
            title={`Step ${number} · ${title} — ${state}`}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span aria-hidden="true">{number}</span>
            <span className="sr-only">{`Step ${number} ${state}`}</span>
          </li>
        );
      })}
    </ol>
  );
}
