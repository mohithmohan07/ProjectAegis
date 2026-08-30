import { useState } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import { AuthProvider } from "../Auth";
import type { UploadJob } from "../types";
import DocumentUpload from "./DocumentUpload";

const streamNdjsonMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  getUploadJob: vi.fn(),
  importConceptCheckpoint: vi.fn(),
  checkpointUrl: vi.fn((id: number) => `/checkpoint/${id}`),
  runDiagnosticsUrl: vi.fn((id: number) => `/diagnostics/${id}`),
  clearConceptCheckpoint: vi.fn(),
  postLearningUpload: vi.fn(),
  authConfig: vi.fn(),
  authMe: vi.fn(),
  authGoogle: vi.fn(),
  authLogout: vi.fn(),
  paths: {
    assessmentConvert: vi.fn((id: number) => `/assessments/${id}/convert`),
    conceptConvert: vi.fn((id: number) => `/concepts/${id}/convert`),
  },
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: streamNdjsonMock,
  SESSION_EXPIRED_EVENT: "aegis:session-expired",
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
  streamNdjsonMock.mockResolvedValue({});
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
  expect(screen.queryByRole("link", {
    name: "Open Google Drive backup folder",
  })).toBeNull();
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

test("restores a saved job only once when the parent callback changes", async () => {
  const saved = restoredJob();
  localStorage.setItem(
    "aegis-upload-job:concepts:post",
    JSON.stringify({
      id: saved.id,
      module: saved.module,
      learning_kind: saved.learning_kind,
      filename: saved.filename,
      created_at: saved.created_at,
    }),
  );
  apiMock.getUploadJob.mockImplementation(async () => ({ ...saved }));

  function UnstableParent() {
    const [restored, setRestored] = useState<UploadJob | null>(null);
    return (
      <RunConsoleProvider>
        <DocumentUpload
          module="concepts"
          conceptKind="post"
          onJob={(job) => setRestored(job)}
        />
        <span>{restored?.filename ?? "not restored"}</span>
      </RunConsoleProvider>
    );
  }

  render(<UnstableParent />);

  expect((await screen.findAllByText("electricity.mmd")).length).toBeGreaterThan(0);
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 20));
  });
  expect(apiMock.getUploadJob).toHaveBeenCalledTimes(1);
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

test("shows configured automatic Drive backup status from auth config", async () => {
  apiMock.authConfig.mockResolvedValue({
    mode: "local",
    google_client_id: "",
    allowed_google_domain: "",
    csrf_token: "csrf",
    drive_checkpoint_backup: {
      enabled: true,
      configured: true,
      auth_mode: "service_account_shared_drive",
      notice: "Backups are restricted to the Aegis folder.",
      state: "succeeded",
      verified: true,
    },
  });
  apiMock.authMe.mockResolvedValue({
    authenticated: true,
    user: {
      sub: "local",
      email: "local@localhost",
      name: "Local mode",
    },
  });
  render(
    <AuthProvider>
      <RunConsoleProvider>
        <DocumentUpload
          module="concepts"
          conceptKind="post"
          externalJob={restoredJob()}
          onJob={vi.fn()}
        />
      </RunConsoleProvider>
    </AuthProvider>,
  );

  expect(await screen.findByText(
    /Automatic Drive backup has been verified; each completed stage is queued/i,
  )).toBeDefined();
  expect(screen.getByText(
    /Backups are restricted to the Aegis folder/i,
  )).toBeDefined();
});

test("locks file-changing controls while parent generation is active", () => {
  const { container } = render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={restoredJob()}
        disabled
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  expect(screen.getByRole("button", { name: "Keep for later" }))
    .toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Discard checkpoint" }))
    .toHaveProperty("disabled", true);
  const replaceInput = container.querySelector(
    'label input[type="file"]:not([accept])',
  ) as HTMLInputElement;
  expect(replaceInput.disabled).toBe(true);
});

test("locks file-changing controls while conversion is active", async () => {
  let resolveConversion!: (result: {
    status: string;
    mmd_text: string;
    mmd_chars: number;
  }) => void;
  streamNdjsonMock.mockReturnValue(new Promise((resolve) => {
    resolveConversion = resolve;
  }));
  const uploaded = {
    ...restoredJob(),
    status: "uploaded",
    checkpoint_available: false,
    mmd_text: "",
  };
  const onJob = vi.fn();
  const { container } = render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={uploaded}
        onJob={onJob}
      />
    </RunConsoleProvider>,
  );

  fireEvent.click(await screen.findByRole("button", {
    name: "Parse source document",
  }));

  await waitFor(() => {
    expect(screen.getByRole("button", { name: "Start over" }))
      .toHaveProperty("disabled", true);
  });
  const replaceInput = container.querySelector(
    'label input[type="file"]:not([accept])',
  ) as HTMLInputElement;
  expect(replaceInput.disabled).toBe(true);

  resolveConversion({
    status: "converted",
    mmd_text: "## Electricity",
    mmd_chars: 14,
  });
  await waitFor(() => {
    expect(onJob).toHaveBeenCalledWith(expect.objectContaining({
      id: uploaded.id,
      status: "converted",
    }));
  });
});
