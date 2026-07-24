import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import type { UploadJob } from "../types";
import DocumentUpload from "./DocumentUpload";

const apiMock = vi.hoisted(() => ({
  getUploadJob: vi.fn(),
  importConceptCheckpoint: vi.fn(),
  checkpointUrl: vi.fn((id: number) => `/checkpoint/${id}`),
  clearConceptCheckpoint: vi.fn(),
  postLearningUpload: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: vi.fn(),
}));

function restoredJob(): UploadJob {
  return {
    id: 42,
    module: "build_concepts",
    upload_type: "document",
    textbook_mode: "",
    learning_kind: "post",
    filename: "electricity.mmd",
    mmd_text: "## Electricity",
    deposit_scope_type: "chapter",
    deposit_scope_ids: [],
    status: "converted",
    result_ids: [],
    detail: "Generation failed: final validation failed at row_index=7",
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
    generation_log: [{
      type: "log",
      level: "error",
      message: "row_index=7; concept='Electric Power'; code='rich_text_format'",
    }],
    created_at: "2026-07-24T10:00:00Z",
  };
}

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
  });
  vi.clearAllMocks();
  apiMock.getUploadJob.mockRejectedValue(new Error("no saved job"));
  apiMock.importConceptCheckpoint.mockResolvedValue(restoredJob());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("restores and displays a portable checkpoint with saved diagnostics", async () => {
  const onJob = vi.fn();
  const { container } = render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        onJob={onJob}
      />
    </RunConsoleProvider>,
  );
  const input = container.querySelector(
    'input[accept*=".aegis-checkpoint.json"]',
  ) as HTMLInputElement;

  fireEvent.change(input, {
    target: {
      files: [
        new File(
          ["{}"],
          "electricity.aegis-checkpoint.json",
          { type: "application/json" },
        ),
      ],
    },
  });

  expect(await screen.findByText("Saved checkpoint at 91%")).toBeDefined();
  expect(screen.getByText(/next run resumes automatically/i)).toBeDefined();
  expect(screen.getByText(/Target: cbse \/ 10 \/ science/i)).toBeDefined();
  const driveLink = screen.getByRole("link", {
    name: "Open Google Drive backup folder",
  });
  expect(driveLink.getAttribute("href")).toBe(
    "https://drive.google.com/drive/folders/1ZrgyXqB339m312XqhxLWMu5Z5H15Ggyo",
  );
  expect(driveLink.querySelector("button")).toBeNull();
  fireEvent.click(screen.getByText("Last saved error details"));
  expect(screen.getByText(/concept='Electric Power'/)).toBeDefined();
  await waitFor(() => {
    expect(apiMock.importConceptCheckpoint).toHaveBeenCalledWith(
      expect.any(File),
      "post",
    );
    expect(onJob).toHaveBeenCalledWith(
      expect.objectContaining({ id: 42 }),
    );
  });
});

test("continues when browser storage is disabled or full", async () => {
  vi.stubGlobal("localStorage", {
    getItem: () => {
      throw new DOMException("Storage disabled", "SecurityError");
    },
    setItem: () => {
      throw new DOMException("Storage full", "QuotaExceededError");
    },
    removeItem: () => {
      throw new DOMException("Storage disabled", "SecurityError");
    },
  });
  const onJob = vi.fn();
  const { container } = render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        onJob={onJob}
      />
    </RunConsoleProvider>,
  );
  const input = container.querySelector(
    'input[accept*=".aegis-checkpoint.json"]',
  ) as HTMLInputElement;

  fireEvent.change(input, {
    target: {
      files: [
        new File(["{}"], "saved.aegis-checkpoint.json", {
          type: "application/json",
        }),
      ],
    },
  });

  expect(await screen.findByText("Saved checkpoint at 91%")).toBeDefined();
  expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ id: 42 }));
});

test("a slow saved-job lookup cannot overwrite a new upload", async () => {
  const staleJob = {
    ...restoredJob(),
    id: 7,
    filename: "stale.mmd",
    created_at: "2026-07-23T09:00:00Z",
  };
  localStorage.setItem(
    "aegis-upload-job:concepts:post",
    JSON.stringify({
      id: staleJob.id,
      module: staleJob.module,
      learning_kind: staleJob.learning_kind,
      filename: staleJob.filename,
      created_at: staleJob.created_at,
    }),
  );
  let resolveSavedJob!: (job: UploadJob) => void;
  apiMock.getUploadJob.mockReturnValue(new Promise<UploadJob>((resolve) => {
    resolveSavedJob = resolve;
  }));
  const newJob = {
    ...restoredJob(),
    id: 99,
    filename: "new-upload.pdf",
    created_at: "2026-07-24T11:00:00Z",
    checkpoint_available: false,
  };
  apiMock.postLearningUpload.mockResolvedValue(newJob);
  const onJob = vi.fn();
  const { container } = render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        onJob={onJob}
      />
    </RunConsoleProvider>,
  );
  expect(screen.getByRole("status").textContent).toMatch(/checking/i);
  const uploadInput = container.querySelector(
    'input[type="file"]:not([accept])',
  ) as HTMLInputElement;
  fireEvent.change(uploadInput, {
    target: {
      files: [new File(["pdf"], "new-upload.pdf", {
        type: "application/pdf",
      })],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByText("new-upload.pdf")).toBeDefined();
  resolveSavedJob(staleJob);
  await Promise.resolve();
  await Promise.resolve();

  expect(screen.queryByText("stale.mmd")).toBeNull();
  expect(onJob).toHaveBeenLastCalledWith(expect.objectContaining({ id: 99 }));
});
