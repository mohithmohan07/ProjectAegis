import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import type { ResumableCheckpoint, UploadJob } from "../types";
import BuildConcepts from "./BuildConcepts";

const apiMock = vi.hoisted(() => ({
  vocab: vi.fn(),
  resumableConceptCheckpoints: vi.fn(),
  clearConceptCheckpoint: vi.fn(),
  getUploadJob: vi.fn(),
  paths: {
    postLearningGenerate: vi.fn((id: number) => `/post/${id}`),
    preLearningGenerate: vi.fn((id: number) => `/pre/${id}`),
    preLearningFromExisting: "/pre-existing",
  },
}));
const streamMock = vi.hoisted(() => vi.fn());

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: streamMock,
}));

vi.mock("../components/DocumentUpload", () => ({
  default: ({ externalJob }: { externalJob?: UploadJob | null }) => (
    <div data-testid="document-upload">
      {externalJob ? `Loaded ${externalJob.filename}` : "No saved upload"}
    </div>
  ),
}));

vi.mock("../components/DirectoryPicker", () => ({
  default: ({
    initialChapterIdentity,
  }: {
    initialChapterIdentity?: Record<string, string>;
  }) => (
    <div data-testid="directory-picker">
      {initialChapterIdentity?.chapter_title
        ? `Target ${initialChapterIdentity.chapter_title}`
        : "Choose target"}
    </div>
  ),
}));

vi.mock("../components/SyllabusUploader", () => ({
  default: () => <div>Syllabus uploader</div>,
}));

vi.mock("../components/ApiUsageSummary", () => ({
  default: () => null,
}));

function savedJob(overrides: Partial<UploadJob> = {}): UploadJob {
  return {
    id: 42,
    module: "build_concepts",
    upload_type: "document",
    textbook_mode: "",
    learning_kind: "post",
    filename: "electricity.pdf",
    mmd_text: "## Electricity",
    deposit_scope_type: "chapter",
    deposit_scope_ids: [],
    status: "converted",
    result_ids: [],
    detail: "Generation checkpoint saved.",
    checkpoint_available: true,
    checkpoint_stage: "post_type_assignment",
    checkpoint_progress: 0.91,
    checkpoint_saved_at: "2026-07-24T10:00:00Z",
    checkpoint_target_identity: {
      board: "cbse",
      grade: "10",
      subject: "science",
      unit: "electricity and magnetism",
      chapter_title: "electricity",
      chapter_code: "ch-11",
    },
    created_at: "2026-07-24T09:00:00Z",
    ...overrides,
  };
}

function savedSummary(
  overrides: Partial<ResumableCheckpoint> = {},
): ResumableCheckpoint {
  const job = savedJob();
  return {
    id: job.id,
    module: job.module,
    learning_kind: job.learning_kind,
    filename: job.filename,
    status: job.status,
    checkpoint_available: Boolean(job.checkpoint_available),
    checkpoint_stage: job.checkpoint_stage,
    checkpoint_progress: job.checkpoint_progress,
    checkpoint_saved_at: job.checkpoint_saved_at,
    checkpoint_target_identity: job.checkpoint_target_identity,
    generation_running: job.generation_running,
    created_at: job.created_at,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  apiMock.vocab.mockResolvedValue({ book_sources: [] });
  apiMock.resumableConceptCheckpoints.mockImplementation(
    async (kind: "post" | "pre") => ({
      items: kind === "post" ? [savedSummary()] : [],
      total: kind === "post" ? 1 : 0,
    }),
  );
  apiMock.getUploadJob.mockImplementation(
    async (_module: string, id: number) => savedJob({ id }),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

function renderPage() {
  return render(
    <RunConsoleProvider>
      <BuildConcepts />
    </RunConsoleProvider>,
  );
}

test("offers a resumable checkpoint and Resume restores setup without generating", async () => {
  renderPage();
  const dialog = await screen.findByRole("dialog", {
    name: "Resume this concept run?",
  });
  expect(dialog.textContent).toContain("post type assignment");
  expect(dialog.textContent).toContain("91%");
  expect(dialog.textContent).toContain("electricity");

  fireEvent.click(screen.getByRole("button", { name: "Resume" }));

  expect(await screen.findByText("Loaded electricity.pdf")).toBeDefined();
  expect(screen.getByText("Target electricity")).toBeDefined();
  expect(apiMock.getUploadJob).toHaveBeenCalledWith("concepts", 42);
  expect(streamMock).not.toHaveBeenCalled();
});

test("Keep for later acknowledges this checkpoint for the browser session", async () => {
  const first = renderPage();
  expect(await screen.findByRole("dialog")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Keep for later" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  first.unmount();

  renderPage();
  await waitFor(() => {
    expect(apiMock.resumableConceptCheckpoints).toHaveBeenCalledTimes(4);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("does not prompt when the owner has no resumable checkpoint", async () => {
  apiMock.resumableConceptCheckpoints.mockResolvedValue({
    items: [],
    total: 0,
  });
  renderPage();
  await waitFor(() => {
    expect(apiMock.resumableConceptCheckpoints).toHaveBeenCalledTimes(2);
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("polls an active run instead of offering a duplicate Resume action", async () => {
  vi.useFakeTimers();
  const active = savedSummary({ generation_running: true });
  apiMock.resumableConceptCheckpoints.mockImplementation(
    async (kind: "post" | "pre") => ({
      items: kind === "post" ? [active] : [],
      total: kind === "post" ? 1 : 0,
    }),
  );
  apiMock.getUploadJob.mockResolvedValue({
    ...active,
    generation_running: false,
  });
  renderPage();

  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByText("Generation is already running")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(apiMock.getUploadJob).toHaveBeenCalledWith("concepts", 42);
  expect(screen.getByRole("button", { name: "Resume" })).toBeDefined();
});
