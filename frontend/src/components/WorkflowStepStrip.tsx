import { currentWorkflowStep, type WorkflowStep } from "../lib/workflowSteps";
import type { UploadJob } from "../types";

const STEPS: Array<{ step: 1 | 2 | 3; number: string; title: string; detail: string }> = [
  {
    step: 1,
    number: "01",
    title: "Generate Concept Files",
    detail: "Choose the run parameters, upload the source and run. The run "
      + "pauses when the Concept files are ready for review.",
  },
  {
    step: 2,
    number: "02",
    title: "Review Concept files & generate Masters",
    detail: "Download and review both Concept files, upload the reviewed "
      + "files, then Generate Master Files.",
  },
  {
    step: 3,
    number: "03",
    title: "Review Master files & publish",
    detail: "Download and review the Master files, upload the reviewed "
      + "Masters, then publish each lane to the database and the CMS.",
  },
];

function stateFor(step: 1 | 2 | 3, current: WorkflowStep): "complete" | "current" | "upcoming" | "historical" {
  if (current === "legacy") return "historical";
  if (current === "done") return "complete";
  if (step < current) return "complete";
  if (step === current) return "current";
  return "upcoming";
}

/**
 * The visible three-step header for Build Concepts. Presentation only: it
 * reads the job's durable workflow marker and never changes it.
 */
export default function WorkflowStepStrip({ job }: { job: UploadJob | null }) {
  const current = currentWorkflowStep(job);
  return (
    <nav className="workflow-steps-wrap" aria-label="Build Concepts steps">
      <ol className="workflow-steps">
        {STEPS.map(({ step, number, title, detail }) => {
          const state = stateFor(step, current);
          return (
            <li
              className={`workflow-step is-${state}`}
              key={step}
              data-testid={`workflow-step-${number}`}
              data-state={state}
              aria-current={state === "current" ? "step" : undefined}
            >
              <span className="workflow-step-number">Step {number}</span>
              <span className="workflow-step-title">{title}</span>
              <span className="workflow-step-detail">{detail}</span>
            </li>
          );
        })}
      </ol>
      {current === "done" && (
        <div className="hint mt-8" role="status" data-testid="workflow-steps-done">
          All three steps are complete for this run: the Master files are
          published to the database and the CMS.
        </div>
      )}
      {current === "legacy" && (
        <div className="hint mt-8" role="note" data-testid="workflow-steps-legacy">
          Historical run: this run predates the three-step workflow and keeps
          its released downloads and explicit publication controls in the
          Run outputs section.
        </div>
      )}
    </nav>
  );
}
