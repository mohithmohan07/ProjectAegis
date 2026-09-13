import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { RunConsoleProvider } from "../RunConsole";
import type {
  ChapterBatchCan,
  ChapterBatchPage,
  ChapterBatchQueue,
  ChapterBatchRow,
} from "../types";
import ChapterBatch from "./ChapterBatch";

/**
 * The three-row hazard.
 *
 * `DocumentUpload` derives ONE localStorage key per (module, kind, owner)
 * with no row scoping (DocumentUpload.tsx:290-291);
 * `ConceptReviewWorkflow`/`MasterReviewWorkflow` render FIXED DOM ids
 * (ConceptReviewWorkflow.tsx:297, MasterReviewWorkflow.tsx:437); and
 * `RunConsole` holds one RunState behind one monotonic run id, so the last
 * row to mount captures the console and detaches the others.
 *
 * Three rows on one page is where all three defects would show. This test
 * pins the shape that avoids them: three independent inputs, three
 * distinct ids, no console, no browser storage.
 */

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

const runConsoleMock = vi.hoisted(() => ({ useRunConsole: vi.fn() }));

vi.mock("../RunConsole", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../RunConsole")>();
  return { ...actual, useRunConsole: runConsoleMock.useRunConsole };
});

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

function row(chapterId: number, name: string): ChapterBatchRow {
  return {
    chapter_id: chapterId,
    chapter_code: `01NCFMATHS_CH0${chapterId}`,
    chapter_title: name,
    chapter_display_name: name,
    board: "NCF", grade: "01", subject: "Maths", unit: "Shapes",
    job_id: null,
    source_filename: "", source_book: "", staged_by_email: "",
    source_staged_at: null,
    state: "no_source", state_label: "No source",
    stage: "", progress: 0, workflow_status: "",
    lanes: [],
    blocked_kind: "", blocked_reason: "", error_message: "",
    pending_decision: null,
    can: can({ upload_source: true }),
    queue: queue(),
    last_actor_email: "", last_actor_act: "", last_actor_at: null,
    updated_at: null,
  };
}

const PAGE: ChapterBatchPage = {
  items: [row(1, "Shapes"), row(2, "Numbers"), row(3, "Measurement")],
  page: 1, page_size: 25, total: 3, total_pages: 1,
  facets: {
    boards: ["NCF"], grades: ["01"], subjects: ["Maths"],
    triples: [{ board: "NCF", grade: "01", subject: "Maths" }],
  },
  states: [{ value: "no_source", label: "No source", tone: "neutral" }],
  queue: { running: 0, queued: 0, blocked: 0, capacity: 2, worker_alive: true },
  server_time: "2026-09-12T00:00:00",
};

/** Every storage accessor at once: the page must touch none of them. */
function spyOnBrowserStorage() {
  return {
    getItem: vi.spyOn(Storage.prototype, "getItem"),
    setItem: vi.spyOn(Storage.prototype, "setItem"),
    removeItem: vi.spyOn(Storage.prototype, "removeItem"),
  };
}

let storage: ReturnType<typeof spyOnBrowserStorage>;

beforeEach(() => {
  apiMock.chapterBatchList.mockReset();
  apiMock.chapterBatchStageSource.mockReset();
  apiMock.vocab.mockReset();
  runConsoleMock.useRunConsole.mockReset();
  apiMock.vocab.mockResolvedValue({ book_sources: ["NCERT", "Seed to Plant"] });
  apiMock.chapterBatchList.mockResolvedValue(PAGE);
  apiMock.chapterBatchStageSource.mockResolvedValue({
    ...row(2, "Numbers"),
    job_id: 71,
    source_filename: "numbers.pdf",
    source_book: "Seed to Plant",
    state: "source_staged",
    state_label: "Source staged",
  });
  storage = spyOnBrowserStorage();
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/chapters"]}>
      <RunConsoleProvider>
        <ChapterBatch />
      </RunConsoleProvider>
    </MemoryRouter>,
  );
}

test("three rows mount three independent uploads with three distinct DOM ids", async () => {
  const { container } = renderPage();
  await screen.findByText("Shapes");

  const inputs = [...container.querySelectorAll('input[type="file"]')];
  expect(inputs).toHaveLength(3);

  const ids = inputs.map((input) => input.id);
  expect(ids).toEqual([
    "chapter-1-upload-source",
    "chapter-2-upload-source",
    "chapter-3-upload-source",
  ]);
  // Every id is scoped by chapter: no two rows can collide.
  expect(new Set(ids).size).toBe(3);
});

test("staging names the publication before it spends the upload", async () => {
  const { container } = renderPage();
  await screen.findByText("Numbers");

  const second = container.querySelector<HTMLInputElement>("#chapter-2-upload-source");
  expect(second).not.toBeNull();
  const file = new File(["%PDF-1.4"], "numbers.pdf", { type: "application/pdf" });
  fireEvent.change(second as HTMLInputElement, { target: { files: [file] } });

  // Picking the file arms the form; nothing is staged yet, because
  // source_book is the run's Concept Source and its extracted
  // Post-Learning Question Source (Q42/Q45) and a blank one blocks the
  // database upload later.
  expect(apiMock.chapterBatchStageSource).not.toHaveBeenCalled();
  const book = await screen.findByLabelText("Source (publication)");
  expect(book.getAttribute("list")).toBe("chapter-book-sources");
  fireEvent.change(book, { target: { value: "Seed to Plant" } });
  fireEvent.change(screen.getByLabelText("Chapter duration (minutes)"), {
    target: { value: "200" },
  });
  fireEvent.click(screen.getByText("Stage source"));

  await waitFor(() => {
    expect(apiMock.chapterBatchStageSource).toHaveBeenCalledTimes(1);
  });
  expect(apiMock.chapterBatchStageSource)
    .toHaveBeenCalledWith(2, file, "Seed to Plant", 200);
});

test("one row's failure stays in that row", async () => {
  apiMock.chapterBatchStageSource.mockRejectedValueOnce(
    new Error("the source could not be staged"),
  );
  const { container } = renderPage();
  await screen.findByText("Numbers");

  const second = container.querySelector<HTMLInputElement>("#chapter-2-upload-source");
  expect(second).not.toBeNull();
  const file = new File(["%PDF-1.4"], "numbers.pdf", { type: "application/pdf" });
  fireEvent.change(second as HTMLInputElement, { target: { files: [file] } });
  fireEvent.click(await screen.findByText("Stage source"));

  await waitFor(() => {
    expect(apiMock.chapterBatchStageSource).toHaveBeenCalledTimes(1);
  });
  expect(apiMock.chapterBatchStageSource).toHaveBeenCalledWith(2, file, "", 0);

  // The error belongs to row 2 alone — the other two inputs are untouched.
  const error = await screen.findByText("Error: the source could not be staged");
  expect(error.id).toBe("chapter-2-upload-source-error");
  expect(container.querySelectorAll(".error-box")).toHaveLength(1);
});

test("the page never calls useRunConsole and never touches browser storage", async () => {
  renderPage();
  await screen.findByText("Shapes");
  await screen.findByText("Numbers");
  await screen.findByText("Measurement");

  expect(runConsoleMock.useRunConsole).not.toHaveBeenCalled();
  expect(storage.getItem).not.toHaveBeenCalled();
  expect(storage.setItem).not.toHaveBeenCalled();
  expect(storage.removeItem).not.toHaveBeenCalled();
});

/* ---------------------------------------------------------------------
   A selection that spans pages (Q66).

   `rows` is only the page on screen. Filtering the selection through it
   counted a row picked on page 1 in the action bar and then never sent
   it once the operator moved to page 2 (verified audit, 13 September
   2026). The page now remembers every selected row's last projection.
   --------------------------------------------------------------------- */

function retryable(chapterId: number, name: string): ChapterBatchRow {
  return {
    ...row(chapterId, name),
    job_id: 40 + chapterId,
    source_filename: `${name.toLowerCase()}.pdf`,
    state: "failed",
    state_label: "Failed",
    can: can({ upload_source: true, retry: true }),
    queue: queue({
      task_id: 100 + chapterId, kind: "step01", state: "failed",
      attempt: 2, failure_code: "attempts_exhausted",
      last_error: "the provider timed out",
    }),
  };
}

const PAGE_ONE: ChapterBatchPage = {
  ...PAGE,
  items: [retryable(1, "Shapes"), row(2, "Numbers"), row(3, "Measurement")],
  page: 1, page_size: 3, total: 5, total_pages: 2,
};

const PAGE_TWO: ChapterBatchPage = {
  ...PAGE,
  items: [retryable(4, "Fractions"), row(5, "Time")],
  page: 2, page_size: 3, total: 5, total_pages: 2,
};

function checkboxFor(container: HTMLElement, chapterId: number): HTMLInputElement {
  const box = container.querySelector<HTMLInputElement>(
    `[data-testid="chapter-row-${chapterId}"] input[type="checkbox"]`,
  );
  expect(box).not.toBeNull();
  return box as HTMLInputElement;
}

test("a row selected on page 1 is still counted and sent from page 2", async () => {
  apiMock.chapterBatchList.mockImplementation(
    async (query: { page?: number }) => (query.page === 2 ? PAGE_TWO : PAGE_ONE),
  );
  apiMock.chapterBatchRetry.mockReset();
  apiMock.chapterBatchRetry.mockResolvedValue({
    step: "", push_group_id: "g-1", results: [],
  });

  const { container } = renderPage();
  await screen.findByText("Shapes");
  fireEvent.click(checkboxFor(container, 1));
  expect(screen.getByTestId("chapter-actionbar").textContent).toContain("1 selected");
  expect(screen.getByTestId("push-retry").textContent).toContain("Retry (1 of 1)");

  fireEvent.click(screen.getByText("Next"));
  await screen.findByText("Fractions");
  expect(screen.queryByText("Shapes")).toBeNull();

  // Still one selected, still retryable, from a page that no longer shows it.
  expect(screen.getByTestId("chapter-actionbar").textContent).toContain("1 selected");
  const retry = screen.getByTestId("push-retry") as HTMLButtonElement;
  expect(retry.textContent).toContain("Retry (1 of 1)");
  expect(retry.disabled).toBe(false);

  fireEvent.click(checkboxFor(container, 4));
  expect(screen.getByTestId("push-retry").textContent).toContain("Retry (2 of 2)");

  fireEvent.click(screen.getByTestId("push-retry"));
  await waitFor(() => {
    expect(apiMock.chapterBatchRetry).toHaveBeenCalledTimes(1);
  });
  expect(apiMock.chapterBatchRetry).toHaveBeenCalledWith([1, 4]);
});

test("Clear forgets a selection made on another page too", async () => {
  apiMock.chapterBatchList.mockImplementation(
    async (query: { page?: number }) => (query.page === 2 ? PAGE_TWO : PAGE_ONE),
  );
  const { container } = renderPage();
  await screen.findByText("Shapes");
  fireEvent.click(checkboxFor(container, 1));
  fireEvent.click(screen.getByText("Next"));
  await screen.findByText("Fractions");
  fireEvent.click(screen.getByText("Clear"));
  expect(screen.queryByTestId("chapter-actionbar")).toBeNull();

  // Back on page 1 the row is unselected: nothing lingers in the memory.
  fireEvent.click(screen.getByText("Previous"));
  await screen.findByText("Shapes");
  expect(checkboxFor(container, 1).checked).toBe(false);
});
