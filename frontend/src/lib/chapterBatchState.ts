/**
 * Chapter batch console — the pure presentation rules.
 *
 * No React, no fetch, no clock. Everything here is a total function over the
 * payload in `docs/chapter-batch-console-contract.md` Appendix A, so the
 * rules that matter most can be asserted directly:
 *
 *   * a queued row shows NO progress bar — a place in a queue is not work
 *     in flight, and a bar at 0% reads as "started";
 *   * a `recovering` row is never "running" — a crashed lease must not
 *     render as a live run (contract §10);
 *   * a `partly_published` row is never green — a queued CMS append is not
 *     a publication (contract §13, "never record false success");
 *   * a `dead` row is never retryable;
 *   * eligibility is read from `row.can`, which the SERVER owns. Nothing
 *     here re-derives "may this row be pushed" from its state.
 *
 * Rule 1 note: none of this reads content. These are scheduling and
 * presentation mechanics over a closed, server-supplied vocabulary.
 */
import { displayLabel } from "./displayLabels";
import type {
  ChapterBatchCan,
  ChapterBatchLane,
  ChapterBatchPage,
  ChapterBatchPushResult,
  ChapterBatchRow,
  ChapterBatchState,
  ChapterBatchStep,
} from "../types";

/** The contract's tone vocabulary, mapped to the existing badge classes. */
export type ChapterBatchTone = "neutral" | "accent" | "green" | "yellow" | "red";

const TONES: ReadonlySet<string> = new Set([
  "neutral", "accent", "green", "yellow", "red",
]);

/**
 * Fallback labels. Used ONLY when the server omits `state_label` and the
 * state is missing from the response's `states[]` list — the server owns
 * the vocabulary and a client that invents one drifts from it silently.
 */
export const STATE_LABELS: Record<ChapterBatchState, string> = {
  no_source: "No source",
  source_staged: "Source staged",
  step01_queued: "Step 01 queued",
  step01_running: "Step 01 running",
  recovering: "Interrupted — recovering",
  concept_review: "Concept files ready for review",
  reviewed: "Reviewed — ready for Step 02",
  step02_queued: "Step 02 queued",
  step02_running: "Step 02 running",
  master_failed: "Master build failed",
  master_review: "Master files ready for review",
  publish_queued: "Publish queued",
  publish_running: "Publishing",
  partly_published: "Partly published",
  published: "Published",
  blocked: "Blocked — needs a person",
  failed: "Failed",
  dead: "Dead — attempts exhausted",
  cancelled: "Cancelled",
  legacy: "Historical run",
};

/** Fallback tones, same caveat as STATE_LABELS. */
export const STATE_TONES: Record<ChapterBatchState, ChapterBatchTone> = {
  no_source: "neutral",
  source_staged: "neutral",
  step01_queued: "accent",
  step01_running: "accent",
  recovering: "yellow",
  concept_review: "yellow",
  reviewed: "accent",
  step02_queued: "accent",
  step02_running: "accent",
  master_failed: "red",
  master_review: "yellow",
  publish_queued: "accent",
  publish_running: "accent",
  // A queued CMS append is not a publication: never green.
  partly_published: "yellow",
  published: "green",
  blocked: "yellow",
  failed: "red",
  dead: "red",
  cancelled: "neutral",
  legacy: "neutral",
};

type StateVocabulary = ChapterBatchPage["states"] | null | undefined;

function vocabularyEntry(state: string, states: StateVocabulary) {
  return states?.find((entry) => entry.value === state);
}

/**
 * The label for one row: the server's own `state_label` first, then the
 * response's `states[]` list, then the local fallback.
 */
export function stateLabel(
  state: string,
  supplied = "",
  states?: StateVocabulary,
): string {
  const own = supplied.trim();
  if (own) return own;
  const entry = vocabularyEntry(state, states);
  if (entry && entry.label.trim()) return entry.label.trim();
  return STATE_LABELS[state as ChapterBatchState] ?? state;
}

/** The tone for one state, preferring the server's `states[]` list. */
export function stateTone(
  state: string,
  states?: StateVocabulary,
): ChapterBatchTone {
  const supplied = vocabularyEntry(state, states)?.tone?.trim();
  if (supplied && TONES.has(supplied)) return supplied as ChapterBatchTone;
  return STATE_TONES[state as ChapterBatchState] ?? "neutral";
}

/** The existing badge classes; `neutral` is the bare badge. */
export function badgeClass(tone: ChapterBatchTone): string {
  return tone === "neutral" ? "badge" : `badge ${tone}`;
}

const QUEUED_STATES: ReadonlySet<ChapterBatchState> = new Set([
  "step01_queued", "step02_queued", "publish_queued",
]);

const RUNNING_STATES: ReadonlySet<ChapterBatchState> = new Set([
  "step01_running", "step02_running", "publish_running",
]);

/**
 * States where the next move belongs to a person and the queue will never
 * make it: a blocker, a failure, an empty row, a review gate waiting on an
 * uploaded file, or a publication that only half landed.
 *
 * `source_staged` and `reviewed` are deliberately absent — those are ready
 * to push, which the row's own primary action already says.
 */
const NEEDS_PERSON_STATES: ReadonlySet<ChapterBatchState> = new Set([
  "no_source", "concept_review", "master_review", "partly_published",
  "master_failed", "blocked", "failed", "dead",
]);

/** Waiting in the queue: a place in line, not work in flight. */
export function rowIsQueued(row: ChapterBatchRow): boolean {
  return QUEUED_STATES.has(row.state) || row.queue?.state === "queued";
}

/**
 * A crashed run: the task still reads `leased`, but the lease ran out, so
 * no process is driving it. Contract §10 — never render this as running.
 */
export function rowIsRecovering(row: ChapterBatchRow): boolean {
  return row.state === "recovering" || Boolean(row.queue?.lease_expired);
}

/** Actually executing right now, on a live lease. */
export function rowIsRunning(row: ChapterBatchRow): boolean {
  if (rowIsRecovering(row)) return false;
  return RUNNING_STATES.has(row.state);
}

/** The queue holds a live task for this row: running, queued or recovering. */
export function rowIsBusy(row: ChapterBatchRow): boolean {
  return rowIsRunning(row) || rowIsQueued(row) || rowIsRecovering(row);
}

/** Any row in flight — the poll interval reads this. */
export function anyBusy(rows: readonly ChapterBatchRow[]): boolean {
  return (rows ?? []).some(rowIsBusy);
}

/** This row is parked until a person acts on it. */
export function rowNeedsPerson(row: ChapterBatchRow): boolean {
  if (row.pending_decision) return true;
  return NEEDS_PERSON_STATES.has(row.state);
}

/**
 * Whether to draw a progress bar at all. Only a live run has meaningful
 * progress: a queued row has not started and a recovering row's last
 * reported fraction describes a process that is gone.
 */
export function rowShowsProgress(row: ChapterBatchRow): boolean {
  return rowIsRunning(row);
}

/** 0..1, clamped; a malformed value renders as an empty bar, not a wild one. */
export function rowProgressValue(row: ChapterBatchRow): number {
  const value = Number(row.progress);
  if (!Number.isFinite(value) || value <= 0) return 0;
  return value >= 1 ? 1 : value;
}

/**
 * Retry eligibility. The server offers the action (`can.retry`); `dead` is
 * the one state the contract declares closed — attempts exhausted or a
 * non-resumable recovery verdict — so the client refuses it even if the
 * flag arrives set. This only ever narrows what the server offered.
 */
export function rowIsRetryable(row: ChapterBatchRow): boolean {
  return Boolean(row.can?.retry) && row.state !== "dead";
}

/**
 * The rows of a selection a given action may actually be sent for, read
 * straight off `row.can`. The server is the authority (contract §11);
 * this never consults `row.state`.
 */
export function rowsAllowing(
  action: keyof ChapterBatchCan,
  rows: readonly ChapterBatchRow[],
): ChapterBatchRow[] {
  return rows.filter((row) => Boolean(row.can?.[action]));
}

/** Which of the selected rows this push button may send. */
export function selectableFor(
  step: ChapterBatchStep,
  rows: readonly ChapterBatchRow[],
): ChapterBatchRow[] {
  return rowsAllowing(step, rows);
}

/** Which of the selected rows may be cancelled. */
export function cancellableFor(
  rows: readonly ChapterBatchRow[],
): ChapterBatchRow[] {
  return rowsAllowing("cancel", rows);
}

/** Which of the selected rows may be retried (never a dead one). */
export function retryableFor(
  rows: readonly ChapterBatchRow[],
): ChapterBatchRow[] {
  return rows.filter(rowIsRetryable);
}

/**
 * The lanes a publish would actually write for one row: the run's
 * AVAILABLE lanes that are not already published. A Post-only run has one
 * available lane and therefore one lane of work — never a hardcoded
 * pre+post pair (contract §10).
 *
 * A lane whose Master is `queued` is still counted: its CMS append has not
 * landed, so publishing again is the converging act the operator wants.
 */
export function lanesToPublish(row: ChapterBatchRow): ChapterBatchLane[] {
  return (row.lanes ?? []).filter(
    (lane) => lane.available && lane.master !== "published",
  );
}

/** Every available lane, published or not. */
export function availableLanes(row: ChapterBatchRow): ChapterBatchLane[] {
  return (row.lanes ?? []).filter((lane) => lane.available);
}

/**
 * True when every AVAILABLE lane has landed in the database and the CMS
 * workbook. A run with only a Post lane reaches this on one lane.
 */
export function allLanesPublished(row: ChapterBatchRow): boolean {
  const lanes = availableLanes(row);
  return lanes.length > 0 && lanes.every((lane) => lane.master === "published");
}

export interface ChapterPublishPlanEntry {
  chapter_id: number;
  label: string;
  lanes: string[];
  database_writes: number;
  cms_appends: number;
}

export interface ChapterPublishPlanSkip {
  chapter_id: number;
  label: string;
  reason: string;
}

export interface ChapterPublishPlan {
  entries: ChapterPublishPlanEntry[];
  skipped: ChapterPublishPlanSkip[];
  chapters: number;
  lanes: number;
  database_writes: number;
  cms_appends: number;
}

/**
 * What a bulk publish will actually write, counted per chapter from that
 * chapter's own available lanes.
 *
 * One lane publication is one database write and one CMS workbook append:
 * the lane's Concept release is published first and its reviewed Master
 * second, in one ordered act (contract §1/§5). So a Post-only chapter
 * counts 1 write and 1 append — not 2, which is what a hardcoded pre+post
 * pair would have claimed in a confirmation dialog no one could check.
 */
export function publishPlan(
  rows: readonly ChapterBatchRow[],
): ChapterPublishPlan {
  const entries: ChapterPublishPlanEntry[] = [];
  const skipped: ChapterPublishPlanSkip[] = [];
  for (const row of rows) {
    const label = chapterLabel(row);
    const lanes = lanesToPublish(row);
    if (lanes.length === 0) {
      skipped.push({
        chapter_id: row.chapter_id,
        label,
        reason: allLanesPublished(row)
          ? "every available lane is already published"
          : "no available lane to publish",
      });
      continue;
    }
    entries.push({
      chapter_id: row.chapter_id,
      label,
      lanes: lanes.map((lane) => lane.lane),
      database_writes: lanes.length,
      cms_appends: lanes.length,
    });
  }
  const lanes = entries.reduce((total, entry) => total + entry.lanes.length, 0);
  return {
    entries,
    skipped,
    chapters: entries.length,
    lanes,
    database_writes: lanes,
    cms_appends: lanes,
  };
}

/**
 * The row's readable identity for a dialog, a receipt line or the table
 * (Q42: a person reads the label, the machine identity stays on the row).
 */
export function chapterLabel(row: ChapterBatchRow): string {
  return displayLabel(
    row.chapter_display_name,
    row.chapter_title,
    row.chapter_code || `Chapter ${row.chapter_id}`,
  );
}

export interface ChapterPushSummary {
  step: ChapterBatchStep;
  total: number;
  queued: number;
  already_queued: number;
  already_running: number;
  refused: number;
  /** Refusal reasons, grouped so one sentence is not printed five times. */
  refusals: Array<{ reason_code: string; reason: string; chapter_ids: number[] }>;
  /** One line for the receipt header. */
  sentence: string;
}

const STEP_WORD: Record<ChapterBatchStep, string> = {
  step01: "Step 01",
  step02: "Step 02",
  publish: "Publish",
};

/** Counts by verdict, for the receipt line after a push. */
export function summarisePush(
  result: ChapterBatchPushResult,
): ChapterPushSummary {
  const results = result.results ?? [];
  const counts = {
    queued: 0,
    already_queued: 0,
    already_running: 0,
    refused: 0,
  };
  const refusals = new Map<string, { reason_code: string; reason: string; chapter_ids: number[] }>();
  for (const outcome of results) {
    if (Object.prototype.hasOwnProperty.call(counts, outcome.verdict)) {
      counts[outcome.verdict] += 1;
    }
    if (outcome.verdict === "refused") {
      const key = `${outcome.reason_code}::${outcome.reason}`;
      const existing = refusals.get(key);
      if (existing) existing.chapter_ids.push(outcome.chapter_id);
      else {
        refusals.set(key, {
          reason_code: outcome.reason_code,
          reason: outcome.reason,
          chapter_ids: [outcome.chapter_id],
        });
      }
    }
  }
  const parts: string[] = [];
  if (counts.queued) parts.push(`${counts.queued} queued`);
  if (counts.already_queued) parts.push(`${counts.already_queued} already queued`);
  if (counts.already_running) parts.push(`${counts.already_running} already running`);
  if (counts.refused) parts.push(`${counts.refused} refused`);
  const step = STEP_WORD[result.step] ?? result.step;
  return {
    step: result.step,
    total: results.length,
    ...counts,
    refusals: [...refusals.values()],
    sentence: parts.length
      ? `${step}: ${parts.join(", ")}.`
      : `${step}: nothing was sent.`,
  };
}

/** "3rd in the queue" for a queued row; "" for everything else. */
export function queuePositionLabel(row: ChapterBatchRow): string {
  const position = row.queue?.position;
  if (!rowIsQueued(row)) return "";
  if (typeof position !== "number" || !Number.isFinite(position) || position < 1) {
    return "";
  }
  return `#${position} in queue`;
}

/** "Attempt 2 of 2" when the row has burned one; "" on a first attempt. */
export function attemptLabel(row: ChapterBatchRow): string {
  const { attempt, max_attempts: max } = row.queue ?? { attempt: 0, max_attempts: 0 };
  if (!Number.isFinite(attempt) || attempt <= 1) return "";
  if (!Number.isFinite(max) || max <= 0) return `Attempt ${attempt}`;
  return `Attempt ${attempt} of ${max}`;
}
