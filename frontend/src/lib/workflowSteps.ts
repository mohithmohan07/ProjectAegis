import type { ReviewWorkflow, UploadJob } from "../types";

/**
 * The owner's three-step Build Concepts presentation (Q49):
 *
 *   Step 01 · Generate Concept Files — parameters, upload, run; the run
 *             pauses when the Concept files are ready for review.
 *   Step 02 · Review the Concept files, upload the reviewed files and
 *             Generate Master Files.
 *   Step 03 · Review the Master files, upload the reviewed Masters and
 *             publish each lane to the database and the CMS.
 *
 * Which step is current is read off the durable `review_workflow` marker
 * (the stream's terminal payload names the same object `concept_review`).
 * Legacy runs without that marker that already finished keep their
 * historical single-surface presentation and report `"legacy"`.
 */
export type WorkflowStep = 1 | 2 | 3 | "done" | "legacy";

const STEP_TWO_STATUSES = new Set([
  "pending_review",
  "reviewed",
  "master_building",
  "master_failed",
]);

const STEP_THREE_STATUSES = new Set(["master_ready"]);

const KNOWN_STATUSES = new Set([
  ...STEP_TWO_STATUSES,
  ...STEP_THREE_STATUSES,
  "published",
]);

type JobLike = UploadJob | Record<string, unknown> | null | undefined;

export function reviewWorkflowMarker(job: JobLike): ReviewWorkflow | null {
  if (!job || typeof job !== "object") return null;
  const record = job as Record<string, unknown>;
  const raw = record.review_workflow ?? record.concept_review;
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? raw as ReviewWorkflow
    : null;
}

/** Lower-cased marker status, or null when the job has no marker. */
export function reviewWorkflowStatus(job: JobLike): string | null {
  const status = reviewWorkflowMarker(job)?.status;
  return typeof status === "string" && status.trim()
    ? status.trim().toLowerCase()
    : null;
}

/** True when the job carries a recognised Concept-first workflow marker. */
export function hasReviewWorkflowMarker(job: JobLike): boolean {
  const status = reviewWorkflowStatus(job);
  return status !== null && KNOWN_STATUSES.has(status);
}

/** Step 03 is open once the Masters exist and stays open after publication
 * so the receipts remain visible. */
export function isMasterReviewStage(job: JobLike): boolean {
  const status = reviewWorkflowStatus(job);
  return status !== null
    && (STEP_THREE_STATUSES.has(status) || status === "published");
}

export function currentWorkflowStep(job: JobLike): WorkflowStep {
  if (!job) return 1;
  const status = reviewWorkflowStatus(job);
  if (status === "published") return "done";
  if (status !== null && STEP_THREE_STATUSES.has(status)) return 3;
  if (status !== null && STEP_TWO_STATUSES.has(status)) return 2;
  const jobStatus = typeof (job as Record<string, unknown>).status === "string"
    ? String((job as Record<string, unknown>).status).toLowerCase()
    : "";
  // The backend's persisted Master-handoff job status, seen on older
  // resumable summaries that omit the marker projection.
  if (jobStatus === "concept_review") return 2;
  if (jobStatus === "generated" || jobStatus === "released") return "legacy";
  return 1;
}
