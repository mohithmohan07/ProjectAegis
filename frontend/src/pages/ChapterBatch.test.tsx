import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { RunConsoleProvider } from "../RunConsole";
import type {
  ChapterBatchCan,
  ChapterBatchLane,
  ChapterBatchPage,
  ChapterBatchQueue,
  ChapterBatchRow,
} from "../types";
import ChapterBatch from "./ChapterBatch";

const apiMock = vi.hoisted(() => ({
  vocab: vi.fn(),
  chapterBatchList: vi.fn(),
  chapterBatchDetail: vi.fn(),
  chapterBatchStageSource: vi.fn(),
  chapterBatchPush: vi.fn(),
  chapterBatchCancel: vi.fn(),
  chapterBatchRetry: vi.fn(),
  chapterBatchUploadConceptReview: vi.fn(),
  chapterBatchUploadMasterReview: vi.fn(),
  chapterBatchEvents: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: vi.fn(),
  isNonTransientStatus: (error: unknown) => {
    const status = (error as { status?: number } | null)?.status;
    return status === 401 || status === 403 || status === 404 || status === 410;
  },
}));

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
    lane: "post", available: true,
    concept_reviewed: false, concept_reviewed_filename: "",
    concept: "available", concept_reason: "",
    master: "ready", master_reason: "", master_version: 1,
    ...overrides,
  };
}

function row(overrides: Partial<ChapterBatchRow> = {}): ChapterBatchRow {
  return {
    chapter_id: 101,
    chapter_code: "01NCFMATHS_CH01",
    chapter_title: "Shapes Around Us",
    chapter_display_name: "Shapes Around Us",
    board: "NCF", grade: "01", subject: "Maths", unit: "Shapes",
    job_id: null,
    source_filename: "", source_book: "", staged_by_email: "",
    source_staged_at: null,
    state: "no_source", state_label: "No source",
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

const STAGED = row({
  chapter_id: 101,
  state: "source_staged",
  state_label: "Source staged",
  source_filename: "shapes.pdf",
  job_id: 55,
  can: can({ step01: true, upload_source: true }),
});

const IN_REVIEW = row({
  chapter_id: 102,
  chapter_code: "01NCFMATHS_CH02",
  chapter_title: "Numbers Beyond 20",
  chapter_display_name: "Numbers Beyond 20",
  state: "concept_review",
  state_label: "Concept files ready for review",
  workflow_status: "pending_review",
  job_id: 56,
  lanes: [lane({ lane: "post" }), lane({ lane: "pre" })],
  can: can({ upload_concept: true, cancel: false }),
});

const POST_ONLY_MASTER = row({
  chapter_id: 103,
  chapter_code: "05CBSESCI_CH03",
  chapter_title: "Measurement",
  chapter_display_name: "Measurement",
  board: "CBSE", grade: "05", subject: "Science", unit: "Measuring",
  state: "master_review",
  state_label: "Master files ready for review",
  workflow_status: "master_ready",
  job_id: 57,
  lanes: [
    lane({ lane: "post", available: true, master: "ready" }),
    lane({ lane: "pre", available: false, master: "none" }),
  ],
  can: can({ publish: true, upload_master: true }),
});

function page(overrides: Partial<ChapterBatchPage> = {}): ChapterBatchPage {
  return {
    items: [STAGED, IN_REVIEW, POST_ONLY_MASTER],
    page: 1, page_size: 25, total: 3, total_pages: 1,
    facets: {
      boards: ["NCF", "CBSE"],
      grades: ["01", "05"],
      subjects: ["Maths", "Science"],
      triples: [
        { board: "NCF", grade: "01", subject: "Maths" },
        { board: "CBSE", grade: "05", subject: "Science" },
      ],
    },
    states: [
      { value: "source_staged", label: "Source staged", tone: "neutral" },
      { value: "concept_review", label: "Concept files ready for review", tone: "yellow" },
      { value: "master_review", label: "Master files ready for review", tone: "yellow" },
      { value: "partly_published", label: "Partly published", tone: "yellow" },
    ],
    queue: {
      running: 1, queued: 2, blocked: 0, capacity: 2, worker_alive: true,
    },
    server_time: "2026-09-12T00:00:00",
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/chapters"]}>
      <RunConsoleProvider>
        <ChapterBatch />
      </RunConsoleProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  apiMock.vocab.mockReset();
  apiMock.vocab.mockResolvedValue({ book_sources: ["NCERT"] });
  apiMock.chapterBatchList.mockReset();
  apiMock.chapterBatchPush.mockReset();
  apiMock.chapterBatchCancel.mockReset();
  apiMock.chapterBatchRetry.mockReset();
  apiMock.chapterBatchDetail.mockReset();
  apiMock.chapterBatchEvents.mockReset();
  apiMock.chapterBatchList.mockResolvedValue(page());
  apiMock.chapterBatchDetail.mockResolvedValue({ row: IN_REVIEW, job: null });
  apiMock.chapterBatchEvents.mockResolvedValue({
    events: [{ type: "log", message: "Reading the source", seq: 1 }],
    next: 4,
    running: true,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

test("renders one row per chapter with the server's state and queue summary", async () => {
  renderPage();

  expect(await screen.findByText("Shapes Around Us")).toBeDefined();
  expect(screen.getByText("Numbers Beyond 20")).toBeDefined();
  expect(screen.getByText("Measurement")).toBeDefined();

  // The server's label, not one the client invented.
  expect(screen.getByTestId("chapter-101-state").textContent).toBe("Source staged");
  expect(screen.getByTestId("chapter-102-state").textContent)
    .toBe("Concept files ready for review");

  const summary = screen.getByTestId("chapter-queue-summary");
  expect(within(summary).getByText("1 running")).toBeDefined();
  expect(within(summary).getByText("2 queued")).toBeDefined();
  expect(within(summary).getByText("capacity 2")).toBeDefined();
  expect(screen.getByTestId("chapter-worker-alive")).toBeDefined();

  // Nothing is running on these rows, so no row draws a progress bar.
  expect(screen.queryByTestId("chapter-101-progress")).toBeNull();
  expect(screen.queryByTestId("chapter-102-progress")).toBeNull();

  // One primary action per row, read from the server's can map.
  expect(screen.getByText("Run Step 01")).toBeDefined();
  expect(screen.getByText("Upload reviewed Concept files")).toBeDefined();
});

test("a queued row shows its position and no progress bar; a running row shows one", async () => {
  apiMock.chapterBatchList.mockResolvedValue(page({
    items: [
      row({
        chapter_id: 201,
        chapter_display_name: "Queued chapter",
        state: "step01_queued",
        state_label: "Step 01 queued",
        progress: 0.4,
        queue: queue({ state: "queued", position: 3, task_id: 9 }),
        can: can({ cancel: true }),
      }),
      row({
        chapter_id: 202,
        chapter_display_name: "Running chapter",
        state: "step01_running",
        state_label: "Step 01 running",
        stage: "Reading the source",
        progress: 0.25,
        queue: queue({ state: "leased", task_id: 10 }),
        can: can({ cancel: true }),
      }),
      row({
        chapter_id: 203,
        chapter_display_name: "Crashed chapter",
        state: "recovering",
        state_label: "Interrupted — recovering",
        progress: 0.9,
        queue: queue({ state: "leased", task_id: 11, lease_expired: true }),
      }),
    ],
  }));
  renderPage();

  expect(await screen.findByText("Queued chapter")).toBeDefined();
  expect(screen.getByTestId("chapter-201-queue-position").textContent)
    .toBe("#3 in queue");
  expect(screen.queryByTestId("chapter-201-progress")).toBeNull();
  expect(screen.getByTestId("chapter-202-progress")).toBeDefined();
  // A crashed lease is "recovering", never a live run with a bar.
  expect(screen.queryByTestId("chapter-203-progress")).toBeNull();
});

test("the filter bar narrows the request and cascades grade and subject", async () => {
  renderPage();
  await screen.findByText("Shapes Around Us");

  apiMock.chapterBatchList.mockResolvedValue(page({
    items: [POST_ONLY_MASTER], total: 1,
  }));
  fireEvent.change(screen.getByLabelText("Board"), { target: { value: "CBSE" } });

  await waitFor(() => {
    expect(apiMock.chapterBatchList).toHaveBeenCalledWith(
      expect.objectContaining({ board: "CBSE" }),
    );
  });
  await waitFor(() => {
    expect(screen.queryByText("Shapes Around Us")).toBeNull();
  });
  expect(screen.getByText("Measurement")).toBeDefined();

  // Grade and subject now offer only what sits under CBSE.
  const grade = screen.getByLabelText("Grade") as HTMLSelectElement;
  expect([...grade.options].map((o) => o.value)).toEqual(["", "05"]);
  const subject = screen.getByLabelText("Subject") as HTMLSelectElement;
  expect([...subject.options].map((o) => o.value)).toEqual(["", "Science"]);

  // The state filter is built from the server's states[] list.
  const state = screen.getByLabelText("State") as HTMLSelectElement;
  expect([...state.options].map((o) => o.value)).toEqual([
    "", "source_staged", "concept_review", "master_review", "partly_published",
  ]);
});

test("selecting rows and pushing sends ONE request with only the eligible rows", async () => {
  apiMock.chapterBatchPush.mockResolvedValue({
    step: "step01",
    push_group_id: "grp-1",
    results: [
      {
        chapter_id: 101, verdict: "queued", reason_code: "", reason: "",
        task_id: 9, position: 1,
        row: {
          ...STAGED,
          state: "step01_queued",
          state_label: "Step 01 queued",
          queue: queue({ state: "queued", position: 1, task_id: 9 }),
        },
      },
    ],
  });
  renderPage();
  await screen.findByText("Shapes Around Us");

  fireEvent.click(screen.getByLabelText("Select Shapes Around Us"));
  fireEvent.click(screen.getByLabelText("Select Measurement"));

  const step01 = screen.getByTestId("push-step01");
  // Two rows selected, but only one of them may be sent Step 01.
  expect(step01.textContent).toBe("Run Step 01 (1 of 2)");

  fireEvent.click(step01);

  await waitFor(() => {
    expect(apiMock.chapterBatchPush).toHaveBeenCalledTimes(1);
  });
  expect(apiMock.chapterBatchPush).toHaveBeenCalledWith("step01", [
    { chapter_id: 101 },
  ]);

  // The freshly projected row is folded straight back into the table.
  expect(await screen.findByTestId("chapter-101-queue-position")).toBeDefined();
});

test("a mixed push receipt renders all four verdicts with the server's reasons", async () => {
  apiMock.chapterBatchPush.mockResolvedValue({
    step: "step01",
    push_group_id: "grp-2",
    results: [
      {
        chapter_id: 101, verdict: "queued", reason_code: "", reason: "",
        task_id: 9, position: 1, row: STAGED,
      },
      {
        chapter_id: 102, verdict: "already_queued", reason_code: "wrong_state",
        reason: "This chapter already has a queued task.",
        task_id: 10, position: 2, row: IN_REVIEW,
      },
      {
        chapter_id: 103, verdict: "already_running", reason_code: "already_live",
        reason: "A run is already live for this chapter.",
        task_id: 11, position: null, row: POST_ONLY_MASTER,
      },
      {
        chapter_id: 104, verdict: "refused", reason_code: "no_source",
        reason: "No source PDF is staged for this chapter.",
        task_id: null, position: null,
        row: row({ chapter_id: 104, chapter_display_name: "Empty chapter" }),
      },
    ],
  });
  renderPage();
  await screen.findByText("Shapes Around Us");

  fireEvent.click(screen.getByLabelText("Select Shapes Around Us"));
  fireEvent.click(screen.getByTestId("push-step01"));

  expect(await screen.findByTestId("chapter-push-receipt")).toBeDefined();
  expect(screen.getByTestId("chapter-push-receipt-line").textContent).toBe(
    "Step 01: 1 queued, 1 already queued, 1 already running, 1 refused.",
  );
  expect(screen.getByTestId("chapter-101-verdict").getAttribute("data-verdict"))
    .toBe("queued");
  expect(screen.getByTestId("chapter-102-verdict").getAttribute("data-verdict"))
    .toBe("already_queued");
  expect(screen.getByTestId("chapter-103-verdict").getAttribute("data-verdict"))
    .toBe("already_running");
  expect(screen.getByTestId("chapter-104-verdict").getAttribute("data-verdict"))
    .toBe("refused");
  // The server's own sentence, verbatim.
  expect(screen.getByText("No source PDF is staged for this chapter."))
    .toBeDefined();
});

test("the publish dialog names the chapters and the exact write counts", async () => {
  apiMock.chapterBatchPush.mockResolvedValue({
    step: "publish",
    push_group_id: "grp-3",
    results: [
      {
        chapter_id: 103, verdict: "queued", reason_code: "", reason: "",
        task_id: 12, position: 1,
        row: { ...POST_ONLY_MASTER, state: "publish_queued" as const },
      },
    ],
  });
  renderPage();
  await screen.findByText("Measurement");

  fireEvent.click(screen.getByLabelText("Select Measurement"));
  fireEvent.click(screen.getByTestId("push-publish"));

  const dialog = await screen.findByTestId("chapter-publish-dialog");
  expect(within(dialog).getByText("Measurement")).toBeDefined();
  // A Post-only run: ONE write and ONE append, never a pre+post pair.
  expect(within(dialog).getByTestId("publish-plan-chapters").textContent).toBe("1");
  expect(within(dialog).getByTestId("publish-plan-lanes").textContent).toBe("1");
  expect(within(dialog).getByTestId("publish-plan-writes").textContent).toBe("1");
  expect(within(dialog).getByTestId("publish-plan-appends").textContent).toBe("1");

  fireEvent.click(screen.getByTestId("chapter-publish-confirm"));
  await waitFor(() => {
    // The lanes the dialog just counted ride the push. The server never
    // defaults a publication target: a publish with no lanes named is
    // refused outright with `no_lanes`, so an id-only body would make this
    // button a no-op.
    expect(apiMock.chapterBatchPush).toHaveBeenCalledWith("publish", [
      { chapter_id: 103, lanes: ["post"] },
    ]);
  });
  await waitFor(() => {
    expect(screen.queryByTestId("chapter-publish-dialog")).toBeNull();
  });
});

test("a dead queue is said plainly instead of looking like progress", async () => {
  apiMock.chapterBatchList.mockResolvedValue(page({
    queue: { running: 0, queued: 4, blocked: 1, capacity: 2, worker_alive: false },
  }));
  renderPage();

  expect(await screen.findByTestId("chapter-worker-dead")).toBeDefined();
  expect(
    screen.getByText(/No worker is draining this queue/),
  ).toBeDefined();
});

test("the poll stops for good on a 403", async () => {
  vi.useFakeTimers();
  const refusal = Object.assign(new Error("not allowed"), { status: 403 });
  apiMock.chapterBatchList.mockRejectedValue(refusal);

  renderPage();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(screen.getByTestId("chapter-poll-stopped")).toBeDefined();
  expect(apiMock.chapterBatchList).toHaveBeenCalledTimes(1);

  // Well past both poll intervals: it must not spin on a refusal.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(apiMock.chapterBatchList).toHaveBeenCalledTimes(1);
});

test("an empty page says so honestly", async () => {
  apiMock.chapterBatchList.mockResolvedValue(page({ items: [], total: 0 }));
  renderPage();
  expect(await screen.findByTestId("chapter-empty")).toBeDefined();
});

test("the drawer's log poll starts when it opens and stops when it closes", async () => {
  vi.useFakeTimers();
  renderPage();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  fireEvent.click(screen.getByText("Numbers Beyond 20"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(screen.getByTestId("chapter-102-drawer")).toBeDefined();
  // Its OWN cursor, starting at 0 — not the list poll's.
  expect(apiMock.chapterBatchEvents).toHaveBeenCalledWith(102, 0);
  expect(screen.getByTestId("chapter-102-log").textContent)
    .toContain("Reading the source");

  const opened = apiMock.chapterBatchEvents.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4100);
  });
  const polled = apiMock.chapterBatchEvents.mock.calls.length;
  expect(polled).toBeGreaterThan(opened);
  // The cursor advances rather than replaying the whole journal.
  expect(apiMock.chapterBatchEvents).toHaveBeenLastCalledWith(102, 4);

  fireEvent.click(screen.getByText("Numbers Beyond 20"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.queryByTestId("chapter-102-drawer")).toBeNull();

  const closed = apiMock.chapterBatchEvents.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  expect(apiMock.chapterBatchEvents.mock.calls.length).toBe(closed);
});

test("a two-lane publish names both lanes and skips an already-published one", async () => {
  const BOTH = row({
    chapter_id: 104,
    chapter_display_name: "Both lanes",
    state: "master_review",
    workflow_status: "master_ready",
    job_id: 58,
    lanes: [
      lane({ lane: "post", available: true, master: "ready" }),
      lane({ lane: "pre", available: true, master: "queued" }),
    ],
    can: can({ publish: true }),
  });
  const DONE = row({
    chapter_id: 105,
    chapter_display_name: "Already published",
    state: "published",
    workflow_status: "published",
    job_id: 59,
    lanes: [
      lane({ lane: "post", available: true, master: "published" }),
      lane({ lane: "pre", available: false, master: "none" }),
    ],
    // The server still offers the act; every lane is already landed.
    can: can({ publish: true }),
  });
  apiMock.chapterBatchList.mockResolvedValue(page({ items: [BOTH, DONE], total: 2 }));
  apiMock.chapterBatchPush.mockResolvedValue({
    step: "publish", push_group_id: "grp-4", results: [],
  });
  renderPage();
  await screen.findByText("Both lanes");

  fireEvent.click(screen.getByLabelText("Select Both lanes"));
  fireEvent.click(screen.getByLabelText("Select Already published"));
  fireEvent.click(screen.getByTestId("push-publish"));

  const dialog = await screen.findByTestId("chapter-publish-dialog");
  expect(within(dialog).getByTestId("publish-plan-lanes").textContent).toBe("2");
  expect(within(dialog).getByTestId("publish-plan-skipped").textContent)
    .toContain("every available lane is already published");

  fireEvent.click(screen.getByTestId("chapter-publish-confirm"));
  await waitFor(() => {
    // A queued CMS append is still work: it rides the push. The chapter
    // with nothing left to write is never sent at all.
    expect(apiMock.chapterBatchPush).toHaveBeenCalledWith("publish", [
      { chapter_id: 104, lanes: ["post", "pre"] },
    ]);
  });
});

test("a cancel receipt names the act the page sent, not an empty step", async () => {
  const QUEUED = row({
    chapter_id: 201,
    chapter_display_name: "Queued chapter",
    state: "step01_queued",
    state_label: "Step 01 queued",
    job_id: 60,
    queue: queue({ state: "queued", position: 1, task_id: 9 }),
    can: can({ cancel: true }),
  });
  apiMock.chapterBatchList.mockResolvedValue(page({ items: [QUEUED], total: 1 }));
  // `/cancel` and `/retry` reuse the push envelope with an EMPTY step.
  apiMock.chapterBatchCancel.mockResolvedValue({
    step: "",
    push_group_id: "",
    results: [
      {
        chapter_id: 201, verdict: "queued", reason_code: "", reason: "",
        task_id: 9, position: null,
        row: { ...QUEUED, state: "cancelled" as const, state_label: "Cancelled" },
      },
    ],
  });
  renderPage();
  await screen.findByText("Queued chapter");

  fireEvent.click(screen.getByLabelText("Select Queued chapter"));
  fireEvent.click(screen.getByTestId("push-cancel"));

  expect(await screen.findByTestId("chapter-push-receipt-line"))
    .toHaveProperty("textContent", "Cancel: 1 queued.");
});

test("a verdict for a chapter the server can no longer project still renders", async () => {
  apiMock.chapterBatchPush.mockResolvedValue({
    step: "step01",
    push_group_id: "grp-5",
    results: [
      {
        chapter_id: 999, verdict: "refused", reason_code: "unknown_chapter",
        reason: "this chapter no longer exists",
        task_id: null, position: null,
        // The server projects the row after the act; a deleted chapter
        // projects to null.
        row: null,
      },
    ],
  });
  renderPage();
  await screen.findByText("Shapes Around Us");

  fireEvent.click(screen.getByLabelText("Select Shapes Around Us"));
  fireEvent.click(screen.getByTestId("push-step01"));

  const verdict = await screen.findByTestId("chapter-999-verdict");
  expect(verdict.textContent).toContain("Chapter 999");
  expect(screen.getByText("this chapter no longer exists")).toBeDefined();
});

test("a row and its open drawer never share an upload id", async () => {
  const { container } = renderPage();
  await screen.findByText("Numbers Beyond 20");

  // Row 102 has both lanes available, so its primary action opens the
  // drawer; row 103 is Post-only and carries its upload inline.
  fireEvent.click(screen.getByText("Measurement"));
  await screen.findByTestId("chapter-103-drawer");

  const ids = [...container.querySelectorAll("input[type=file]")]
    .map((input) => input.id);
  expect(new Set(ids).size).toBe(ids.length);
  expect(ids).toContain("chapter-103-upload-master-post");
  expect(ids).toContain("chapter-103-drawer-upload-master-post");
  // A lane this run does not have is never offered a reviewed upload.
  expect(ids).not.toContain("chapter-103-drawer-upload-master-pre");
  expect(ids).not.toContain("chapter-103-drawer-upload-concept-pre");
});
