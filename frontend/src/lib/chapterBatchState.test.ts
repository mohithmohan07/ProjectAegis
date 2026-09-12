import { expect, test } from "vitest";
import type {
  ChapterBatchCan,
  ChapterBatchLane,
  ChapterBatchPage,
  ChapterBatchPushResult,
  ChapterBatchQueue,
  ChapterBatchRow,
  ChapterBatchState,
} from "../types";
import {
  STATE_LABELS,
  STATE_TONES,
  allLanesPublished,
  anyBusy,
  attemptLabel,
  badgeClass,
  cancellableFor,
  chapterLabel,
  lanesToPublish,
  publishPlan,
  queuePositionLabel,
  retryableFor,
  rowIsBusy,
  rowIsRecovering,
  rowIsRetryable,
  rowIsRunning,
  rowNeedsPerson,
  rowProgressValue,
  rowShowsProgress,
  rowsAllowing,
  selectableFor,
  stateLabel,
  stateTone,
  summarisePush,
} from "./chapterBatchState";

const ALL_STATES: ChapterBatchState[] = [
  "no_source", "source_staged", "step01_queued", "step01_running",
  "recovering", "concept_review", "reviewed", "step02_queued",
  "step02_running", "master_failed", "master_review", "publish_queued",
  "publish_running", "partly_published", "published", "blocked",
  "failed", "dead", "cancelled", "legacy",
];

function can(overrides: Partial<ChapterBatchCan> = {}): ChapterBatchCan {
  return {
    step01: false, step02: false, publish: false,
    cancel: false, retry: false,
    upload_source: false, upload_concept: false, upload_master: false,
    ...overrides,
  };
}

function queue(overrides: Partial<ChapterBatchQueue> = {}): ChapterBatchQueue {
  return {
    task_id: null, kind: null, state: null, position: null,
    attempt: 0, max_attempts: 2,
    blocked_kind: "", failure_code: "", last_error: "",
    enqueued_by_email: "", enqueued_at: null, started_at: null,
    lease_expired: false,
    ...overrides,
  };
}

function lane(overrides: Partial<ChapterBatchLane> = {}): ChapterBatchLane {
  return {
    lane: "post",
    available: true,
    concept_reviewed: false,
    concept_reviewed_filename: "",
    concept: "available",
    concept_reason: "",
    master: "ready",
    master_reason: "",
    master_version: 1,
    ...overrides,
  };
}

function row(overrides: Partial<ChapterBatchRow> = {}): ChapterBatchRow {
  return {
    chapter_id: 1,
    chapter_code: "01NCFMATHS_CH01",
    chapter_title: "Shapes Around Us",
    chapter_display_name: "Shapes Around Us",
    board: "NCF", grade: "01", subject: "Maths", unit: "Shapes",
    job_id: null,
    source_filename: "", source_book: "", staged_by_email: "",
    source_staged_at: null,
    state: "no_source",
    state_label: "",
    stage: "", progress: 0, workflow_status: "",
    lanes: [],
    blocked_kind: "", blocked_reason: "", error_message: "",
    pending_decision: null,
    can: can(),
    queue: queue(),
    last_actor_email: "", last_actor_act: "", last_actor_at: null,
    updated_at: null,
    ...overrides,
  };
}

const VOCABULARY: ChapterBatchPage["states"] = [
  { value: "published", label: "Published to the database", tone: "green" },
  { value: "partly_published", label: "Partly published", tone: "yellow" },
  { value: "recovering", label: "Interrupted — recovering", tone: "yellow" },
];

/* ----------------------------- vocabulary ----------------------------- */

test("the fallback tables cover the contract's closed state set", () => {
  for (const state of ALL_STATES) {
    expect(STATE_LABELS[state], state).toBeTruthy();
    expect(STATE_TONES[state], state).toBeTruthy();
  }
  expect(Object.keys(STATE_LABELS).sort()).toEqual([...ALL_STATES].sort());
  expect(Object.keys(STATE_TONES).sort()).toEqual([...ALL_STATES].sort());
});

test("the server's own label wins over the vocabulary and the fallback", () => {
  expect(stateLabel("published", "Published · 2 lanes", VOCABULARY))
    .toBe("Published · 2 lanes");
  // No state_label: the response's states[] list is next.
  expect(stateLabel("published", "", VOCABULARY)).toBe("Published to the database");
  // Neither: only then the local fallback.
  expect(stateLabel("published", "", [])).toBe(STATE_LABELS.published);
  // Not even a known state: render what the server said rather than "".
  expect(stateLabel("something_new", "", [])).toBe("something_new");
});

test("tone comes from the server, and an unknown tone falls back", () => {
  expect(stateTone("published", VOCABULARY)).toBe("green");
  expect(stateTone("published", [
    { value: "published", label: "Published", tone: "chartreuse" },
  ])).toBe("green");
  expect(stateTone("failed", [])).toBe("red");
  expect(stateTone("mystery", [])).toBe("neutral");
});

test("a queued CMS append is never green", () => {
  // Contract §10/§13: partly_published is not a publication.
  expect(STATE_TONES.partly_published).not.toBe("green");
  expect(stateTone("partly_published", VOCABULARY)).not.toBe("green");
  expect(badgeClass(stateTone("partly_published", VOCABULARY)))
    .toBe("badge yellow");
  expect(badgeClass("neutral")).toBe("badge");
  expect(badgeClass("green")).toBe("badge green");
});

/* ------------------------------ row rules ----------------------------- */

test("a queued row shows no progress bar", () => {
  for (const state of ["step01_queued", "step02_queued", "publish_queued"] as const) {
    const queued = row({
      state,
      progress: 0.42,
      queue: queue({ state: "queued", position: 3 }),
    });
    expect(rowShowsProgress(queued), state).toBe(false);
    expect(rowIsRunning(queued), state).toBe(false);
    expect(rowIsBusy(queued), state).toBe(true);
  }
});

test("a recovering row is never running", () => {
  const crashed = row({
    state: "recovering",
    progress: 0.8,
    queue: queue({ state: "leased", lease_expired: true }),
  });
  expect(rowIsRecovering(crashed)).toBe(true);
  expect(rowIsRunning(crashed)).toBe(false);
  expect(rowShowsProgress(crashed)).toBe(false);
  expect(rowIsBusy(crashed)).toBe(true);

  // An expired lease outranks a state that still says "running".
  const stale = row({
    state: "step01_running",
    queue: queue({ state: "leased", lease_expired: true }),
  });
  expect(rowIsRunning(stale)).toBe(false);
  expect(rowShowsProgress(stale)).toBe(false);
});

test("a live run is the only thing that shows progress", () => {
  const live = row({
    state: "step02_running",
    progress: 0.5,
    queue: queue({ state: "leased" }),
  });
  expect(rowIsRunning(live)).toBe(true);
  expect(rowShowsProgress(live)).toBe(true);
  expect(rowProgressValue(live)).toBe(0.5);
  expect(rowProgressValue(row({ progress: 4 }))).toBe(1);
  expect(rowProgressValue(row({ progress: Number.NaN }))).toBe(0);
  expect(rowProgressValue(row({ progress: -1 }))).toBe(0);
});

test("anyBusy drives the poll interval", () => {
  expect(anyBusy([row({ state: "published" }), row({ state: "no_source" })]))
    .toBe(false);
  expect(anyBusy([row({ state: "published" }), row({ state: "step01_running" })]))
    .toBe(true);
  expect(anyBusy([])).toBe(false);
});

test("rowNeedsPerson marks the rows the queue will never move", () => {
  expect(rowNeedsPerson(row({ state: "concept_review" }))).toBe(true);
  expect(rowNeedsPerson(row({ state: "master_review" }))).toBe(true);
  expect(rowNeedsPerson(row({ state: "blocked" }))).toBe(true);
  expect(rowNeedsPerson(row({ state: "partly_published" }))).toBe(true);
  expect(rowNeedsPerson(row({ state: "dead" }))).toBe(true);
  expect(rowNeedsPerson(row({ state: "no_source" }))).toBe(true);
  expect(rowNeedsPerson(row({ state: "published" }))).toBe(false);
  expect(rowNeedsPerson(row({ state: "step01_running" }))).toBe(false);
  expect(rowNeedsPerson(row({ state: "source_staged" }))).toBe(false);
  // A pause the engine recorded needs a person whatever the row state is.
  expect(rowNeedsPerson(row({
    state: "step01_running",
    pending_decision: {
      decision_id: "d1", kind: "topic_split", question: "Which topic?", companions: 2,
    },
  }))).toBe(true);
});

test("a dead row is never retryable, even if the flag arrives set", () => {
  expect(rowIsRetryable(row({ state: "failed", can: can({ retry: true }) }))).toBe(true);
  expect(rowIsRetryable(row({ state: "dead", can: can({ retry: true }) }))).toBe(false);
  expect(rowIsRetryable(row({ state: "failed", can: can({ retry: false }) }))).toBe(false);

  const rows = [
    row({ chapter_id: 1, state: "failed", can: can({ retry: true }) }),
    row({ chapter_id: 2, state: "dead", can: can({ retry: true }) }),
  ];
  expect(retryableFor(rows).map((r) => r.chapter_id)).toEqual([1]);
});

/* --------------------------- server authority -------------------------- */

test("selectableFor reads row.can and never re-derives from state", () => {
  const rows = [
    row({ chapter_id: 1, state: "source_staged", can: can({ step01: true }) }),
    row({ chapter_id: 2, state: "source_staged", can: can({ step01: false }) }),
    // Deliberately contradictory: the state says published, the server
    // still offers step01. The server is the authority (contract §11).
    row({ chapter_id: 3, state: "published", can: can({ step01: true }) }),
  ];
  expect(selectableFor("step01", rows).map((r) => r.chapter_id)).toEqual([1, 3]);
  expect(selectableFor("step02", rows)).toEqual([]);
  expect(selectableFor("publish", rows)).toEqual([]);

  const cancellable = [
    row({ chapter_id: 4, state: "step01_queued", can: can({ cancel: true }) }),
    row({ chapter_id: 5, state: "step01_running", can: can({ cancel: false }) }),
  ];
  expect(cancellableFor(cancellable).map((r) => r.chapter_id)).toEqual([4]);
  expect(rowsAllowing("upload_concept", cancellable)).toEqual([]);
});

/* ------------------------------ publishing ----------------------------- */

test("publishPlan counts a POST-ONLY run as one write and one append", () => {
  const postOnly = row({
    chapter_id: 7,
    chapter_title: "Numbers Beyond 20",
    chapter_display_name: "Numbers Beyond 20",
    state: "master_review",
    can: can({ publish: true }),
    lanes: [
      lane({ lane: "post", available: true, master: "ready" }),
      lane({ lane: "pre", available: false, master: "none" }),
    ],
  });
  expect(lanesToPublish(postOnly).map((l) => l.lane)).toEqual(["post"]);

  const plan = publishPlan([postOnly]);
  expect(plan.chapters).toBe(1);
  expect(plan.lanes).toBe(1);
  expect(plan.database_writes).toBe(1);
  expect(plan.cms_appends).toBe(1);
  expect(plan.entries[0]).toMatchObject({
    chapter_id: 7,
    label: "Numbers Beyond 20",
    lanes: ["post"],
    database_writes: 1,
    cms_appends: 1,
  });
  expect(plan.skipped).toEqual([]);

  // And a Post-only run must be able to reach done.
  expect(allLanesPublished(postOnly)).toBe(false);
  expect(allLanesPublished(row({
    lanes: [
      lane({ lane: "post", available: true, master: "published" }),
      lane({ lane: "pre", available: false, master: "none" }),
    ],
  }))).toBe(true);
});

test("publishPlan sums per chapter from each chapter's own lanes", () => {
  const both = row({
    chapter_id: 1,
    lanes: [lane({ lane: "post" }), lane({ lane: "pre" })],
  });
  const postOnly = row({ chapter_id: 2, lanes: [lane({ lane: "post" })] });
  const halfDone = row({
    chapter_id: 3,
    lanes: [
      lane({ lane: "post", master: "published" }),
      // A queued CMS append has not landed: publishing again converges.
      lane({ lane: "pre", master: "queued" }),
    ],
  });
  const done = row({
    chapter_id: 4,
    lanes: [lane({ lane: "post", master: "published" })],
  });

  const plan = publishPlan([both, postOnly, halfDone, done]);
  expect(plan.chapters).toBe(3);
  expect(plan.lanes).toBe(4);
  expect(plan.database_writes).toBe(4);
  expect(plan.cms_appends).toBe(4);
  expect(plan.entries.map((e) => e.lanes)).toEqual([
    ["post", "pre"], ["post"], ["pre"],
  ]);
  expect(plan.skipped).toEqual([
    {
      chapter_id: 4,
      label: "Shapes Around Us",
      reason: "every available lane is already published",
    },
  ]);
});

test("publishPlan never assumes a pre+post pair", () => {
  const noLanes = row({ chapter_id: 9, lanes: [] });
  const plan = publishPlan([noLanes]);
  expect(plan.chapters).toBe(0);
  expect(plan.database_writes).toBe(0);
  expect(plan.skipped[0].reason).toBe("no available lane to publish");
});

/* ------------------------------- receipts ------------------------------ */

function outcome(
  chapterId: number,
  verdict: "queued" | "already_queued" | "already_running" | "refused",
  reasonCode = "",
  reason = "",
  position: number | null = null,
) {
  return {
    chapter_id: chapterId,
    verdict,
    reason_code: reasonCode,
    reason,
    task_id: verdict === "queued" ? chapterId : null,
    position,
    row: row({ chapter_id: chapterId }),
  };
}

test("summarisePush counts every verdict and groups the refusals", () => {
  const result: ChapterBatchPushResult = {
    step: "step01",
    push_group_id: "grp-1",
    results: [
      outcome(1, "queued", "", "", 1),
      outcome(2, "queued", "", "", 2),
      outcome(3, "already_queued"),
      outcome(4, "already_running"),
      outcome(5, "refused", "no_source", "No source PDF is staged for this chapter."),
      outcome(6, "refused", "no_source", "No source PDF is staged for this chapter."),
      outcome(7, "refused", "blocked", "This row is blocked and needs a person."),
    ],
  };
  const summary = summarisePush(result);
  expect(summary.total).toBe(7);
  expect(summary.queued).toBe(2);
  expect(summary.already_queued).toBe(1);
  expect(summary.already_running).toBe(1);
  expect(summary.refused).toBe(3);
  expect(summary.sentence).toBe(
    "Step 01: 2 queued, 1 already queued, 1 already running, 3 refused.",
  );
  expect(summary.refusals).toEqual([
    {
      reason_code: "no_source",
      reason: "No source PDF is staged for this chapter.",
      chapter_ids: [5, 6],
    },
    {
      reason_code: "blocked",
      reason: "This row is blocked and needs a person.",
      chapter_ids: [7],
    },
  ]);
});

test("summarisePush is honest about an empty push", () => {
  const summary = summarisePush({
    step: "publish", push_group_id: "grp-2", results: [],
  });
  expect(summary.total).toBe(0);
  expect(summary.sentence).toBe("Publish: nothing was sent.");
});

/* -------------------------------- labels ------------------------------- */

test("queuePositionLabel only speaks for a queued row", () => {
  expect(queuePositionLabel(row({
    state: "step01_queued", queue: queue({ state: "queued", position: 3 }),
  }))).toBe("#3 in queue");
  expect(queuePositionLabel(row({
    state: "step01_running", queue: queue({ state: "leased", position: 3 }),
  }))).toBe("");
  expect(queuePositionLabel(row({
    state: "step01_queued", queue: queue({ state: "queued", position: null }),
  }))).toBe("");
});

test("attemptLabel stays quiet on a first attempt", () => {
  expect(attemptLabel(row({ queue: queue({ attempt: 1, max_attempts: 2 }) }))).toBe("");
  expect(attemptLabel(row({ queue: queue({ attempt: 2, max_attempts: 2 }) })))
    .toBe("Attempt 2 of 2");
});

test("chapterLabel is the readable label, not the machine identity", () => {
  expect(chapterLabel(row({
    chapter_display_name: "",
    chapter_title: "Measurement (01NCFMATHS_CH04)",
  }))).toBe("Measurement");
  expect(chapterLabel(row({
    chapter_display_name: "",
    chapter_title: "",
    chapter_code: "01NCFMATHS_CH04",
  }))).toBe("01NCFMATHS_CH04");
});
