import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { RunConsoleProvider } from "../RunConsole";
import type {
  OpenAIUsage,
  PendingSemanticDecision,
  ResumableCheckpoint,
  Scope,
  UploadJob,
} from "../types";
import BuildConcepts from "./BuildConcepts";

const apiMock = vi.hoisted(() => ({
  vocab: vi.fn(),
  getModelProvider: vi.fn(() => Promise.resolve({
    provider: "openai",
    model: "gpt-5.6-luna",
    openai_available: true,
    gemini_available: false,
    gemini_model: "gemini-3.6-flash",
    openai_model: "gpt-5.6-luna",
    note: "",
  })),
  setModelProvider: vi.fn(),
  resumableConceptCheckpoints: vi.fn(),
  clearConceptCheckpoint: vi.fn(),
  getUploadJob: vi.fn(),
  submitConceptDecision: vi.fn(),
  conceptReleaseUrl: vi.fn((id: number) => `/release/${id}.xlsx`),
  conceptReleaseBulkImportUrl: vi.fn(
    (id: number) => `/release/${id}-bulk-import.xlsx`,
  ),
  inventoryCsvUrl: vi.fn((id: number) => `/inventory/${id}.csv`),
  listConceptRevisions: vi.fn(),
  submitConceptRevision: vi.fn(),
  paths: {
    postLearningGenerate: vi.fn((id: number) => `/post/${id}`),
  },
}));
const streamMock = vi.hoisted(() => vi.fn());

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: streamMock,
}));

vi.mock("../components/DocumentUpload", () => ({
  default: ({
    conceptKind,
    externalJob,
    onJob,
  }: {
    conceptKind?: "post" | "pre";
    externalJob?: UploadJob | null;
    onJob: (job: UploadJob | null) => void;
  }) => (
    <div data-testid="document-upload">
      {externalJob ? `Loaded ${externalJob.filename}` : "No saved upload"}
      {externalJob && (
        <>
          <button
            type="button"
            onClick={() => onJob({
              ...externalJob,
              filename: `${conceptKind}-replacement.pdf`,
              pending_decision: null,
            })}
          >
            Replace {conceptKind} upload
          </button>
          <button
            type="button"
            onClick={() => onJob({
              ...externalJob,
              checkpoint_available: false,
              pending_decision: null,
            })}
          >
            Discard {conceptKind} checkpoint
          </button>
        </>
      )}
      <button type="button" onClick={() => onJob(null)}>
        Clear {conceptKind} upload
      </button>
    </div>
  ),
}));

vi.mock("../components/DirectoryPicker", () => ({
  default: ({
    initialChapterIdentity,
    onScope,
  }: {
    initialChapterIdentity?: Record<string, string>;
    onScope?: (scope: Scope) => void;
  }) => (
    <>
      <div data-testid="directory-picker">
        {initialChapterIdentity?.chapter_title
          ? `Target ${initialChapterIdentity.chapter_title}`
          : "Choose target"}
      </div>
      <button
        type="button"
        onClick={() => onScope?.({
          type: "chapter",
          ids: [11],
          label: "Electricity",
        })}
      >
        Select Electricity target
      </button>
    </>
  ),
}));

vi.mock("../components/SyllabusUploader", () => ({
  default: () => <div>Syllabus uploader</div>,
}));

vi.mock("../components/ApiUsageSummary", () => ({
  default: ({
    usage,
    cumulative,
    resumed,
  }: {
    usage?: OpenAIUsage;
    cumulative?: boolean;
    resumed?: boolean;
  }) => usage
    ? (
      <div data-testid="usage-presentation">
        {cumulative ? "Cumulative" : "Run"} usage
        {resumed ? " · Resumed" : ""}
        {` · ${usage.total_tokens} tokens`}
      </div>
    )
    : null,
}));

function cumulativeUsage(totalTokens = 900): OpenAIUsage {
  return {
    model: "gpt-5.4-mini-2026-03-17",
    request_count: 4,
    input_tokens: totalTokens - 100,
    cached_input_tokens: 100,
    uncached_input_tokens: totalTokens - 200,
    output_tokens: 100,
    reasoning_tokens: 0,
    total_tokens: totalTokens,
    estimated_cost_usd: 0.01,
  };
}

function semanticDecisionFixture(
  overrides: Partial<PendingSemanticDecision> = {},
): PendingSemanticDecision {
  return {
    decision_id: "phase33-host-abc123",
    kind: "phase33_type_host_semantic_conflict",
    phase: "3.3",
    conflict:
      "The existing Renan concept covers attributes of nationhood but not "
      + "why nations safeguard liberty.",
    item: {
      unit_id: "ASSIGNMENT-0002",
      type_id: "TYPE-0002",
      type_title: "Explain Renan's idea of a nation",
      qids: ["Q-0002"],
      questions: ["Why, in Renan's view, are nations important?"],
      topic: "The French Revolution and the Idea of the Nation",
    },
    candidates: [
      {
        concept_id: "HOST-CONCEPT-0001",
        title: "Renan's Attributes of Nationhood",
        topic: "The French Revolution and the Idea of the Nation",
        coverage: "Attributes of a nation",
        gap: "Importance of nations as safeguards of liberty",
      },
      {
        concept_id: "HOST-CONCEPT-0007",
        title: "Liberty and the Nation State",
        topic: "The French Revolution and the Idea of the Nation",
        coverage: "Liberty",
        gap: "",
      },
    ],
    evidence: [
      {
        page: "8",
        label: "BLK-00102",
        text: "Their existence is a guarantee of liberty.",
      },
    ],
    options: [
      {
        choice: "expand_existing",
        label: "Expand the existing concept",
        recommended: true,
        target_concept_id: "HOST-CONCEPT-0001",
      },
      {
        choice: "create_new",
        label: "Create a separate source-grounded concept",
        recommended: false,
      },
      {
        choice: "select_existing",
        label: "Select another existing concept",
        recommended: false,
      },
      {
        choice: "custom_instruction",
        label: "Give a custom instruction",
        recommended: false,
      },
    ],
    cumulative_usage: cumulativeUsage(1991461),
    ...overrides,
  };
}

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
  streamMock.mockReset();
  window.sessionStorage.clear();
  apiMock.vocab.mockResolvedValue({ book_sources: [] });
  // Single lane. The mock answers whatever lane it is asked for, so a
  // re-introduced pre-lane call would show up as an extra call rather than
  // being silently absorbed by a lane-branched fixture.
  apiMock.resumableConceptCheckpoints.mockResolvedValue({
    items: [savedSummary()],
    total: 1,
  });
  apiMock.getUploadJob.mockImplementation(
    async (_module: string, id: number) => savedJob({ id }),
  );
  apiMock.submitConceptDecision.mockResolvedValue({
    status: "decision_recorded",
    resume_required: true,
    resolved_decision: {},
  });
  apiMock.listConceptRevisions.mockResolvedValue({ job_id: 1, revisions: [] });
});

afterEach(() => {
  vi.useRealTimers();
});

function renderPage() {
  // ConceptReviewPanel deep-links to the release-review page, so the page
  // needs a Router here exactly as in the app.
  return render(
    <MemoryRouter>
      <RunConsoleProvider>
        <BuildConcepts />
      </RunConsoleProvider>
    </MemoryRouter>,
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
  // Two mounts × one lane: discovery ran again on the second mount (so it is
  // still working), and the acknowledgement — not a missing call — is what
  // suppresses the dialog.
  await waitFor(() => {
    expect(apiMock.resumableConceptCheckpoints).toHaveBeenCalledTimes(2);
  });
  expect(apiMock.resumableConceptCheckpoints).toHaveBeenCalledWith("post");
  expect(apiMock.resumableConceptCheckpoints).not.toHaveBeenCalledWith("pre");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("does not prompt when the owner has no resumable checkpoint", async () => {
  apiMock.resumableConceptCheckpoints.mockResolvedValue({
    items: [],
    total: 0,
  });
  renderPage();
  // Discovery ran — exactly once, on the surviving post lane — and found
  // nothing to offer.
  await waitFor(() => {
    expect(apiMock.resumableConceptCheckpoints).toHaveBeenCalledTimes(1);
  });
  expect(apiMock.resumableConceptCheckpoints).toHaveBeenCalledWith("post");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("polls an active run instead of offering a duplicate Resume action", async () => {
  vi.useFakeTimers();
  const active = savedSummary({ generation_running: true });
  apiMock.resumableConceptCheckpoints.mockResolvedValue({
    items: [active],
    total: 1,
  });
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

test("checkpoint recovery presents usage as one resumed cumulative file total", async () => {
  const usage = cumulativeUsage();
  apiMock.getUploadJob.mockImplementation(
    async (_module: string, id: number) => savedJob({
      id,
      openai_usage: usage,
    }),
  );
  streamMock.mockResolvedValue({
    job_id: 42,
    concept_ids: [],
    inventory_items: 0,
    openai_usage: cumulativeUsage(1250),
  });
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(await screen.findByText("Loaded electricity.pdf")).toBeDefined();
  expect((await screen.findByTestId("usage-presentation")).textContent).toBe(
    "Cumulative usage · Resumed · 900 tokens",
  );

  fireEvent.click(screen.getByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText(
    "Concepts written to the Bulk Import workbook (append-only)",
  )).toBeDefined();
  expect(screen.getByTestId("usage-presentation").textContent).toBe(
    "Cumulative usage · Resumed · 1250 tokens",
  );
  expect(streamMock).toHaveBeenCalledTimes(1);
});

test("refreshes a rejected 98% checkpoint to the retained 91% stage", async () => {
  const finalCheckpoint = savedJob({
    checkpoint_stage: "final_content_ready",
    checkpoint_progress: 0.98,
  });
  const retainedCheckpoint = savedJob({
    checkpoint_stage: "post_type_assignment",
    checkpoint_progress: 0.91,
  });
  apiMock.resumableConceptCheckpoints.mockResolvedValue({
    items: [savedSummary({
      checkpoint_stage: "final_content_ready",
      checkpoint_progress: 0.98,
    })],
    total: 1,
  });
  apiMock.getUploadJob
    .mockResolvedValueOnce(finalCheckpoint)
    .mockResolvedValueOnce(retainedCheckpoint);
  streamMock.mockRejectedValue(
    new Error("final validation failed; prior checkpoint retained"),
  );
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(await screen.findByText("Loaded electricity.pdf")).toBeDefined();
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 98% checkpoint",
  }));

  expect(await screen.findByText(
    /final validation failed; prior checkpoint retained/,
  )).toBeDefined();
  expect(await screen.findByRole("button", {
    name: "Resume from 91% checkpoint",
  })).toBeDefined();
  expect(apiMock.getUploadJob).toHaveBeenCalledTimes(2);
});

test("a successfully agent-resolved run completes without rendering a pause", async () => {
  streamMock.mockResolvedValueOnce({
    job_id: 42,
    concept_ids: [],
    inventory_items: 0,
    autonomous_resolution: {
      status: "resolved",
      resolver_version: "aegis-autonomous-resolution-v2",
    },
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText(
    "Concepts written to the Bulk Import workbook (append-only)",
  )).toBeDefined();
  expect(screen.queryByRole("heading", { name: "Paused for your decision" }))
    .toBeNull();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();
  expect(streamMock).toHaveBeenCalledTimes(1);
});

// --------------------------------------------------------------------------
// No selection screen mid-run
// --------------------------------------------------------------------------
//
// The 81%/89% pause used to hand the user a chooser: pick a concept, a Type,
// a topic, a repair. Generation is release-first now, so a semantic issue
// travels into the release with its evidence instead of becoming a question.
// These pin that there is nothing left to click.

test("a released run shows its output, never a chooser", async () => {
  streamMock.mockResolvedValue({
    job_id: 42,
    status: "released",
    released: true,
    row_count: 23,
    issue_count: 2,
    detail: "Released 23 concept row(s) for review; 2 issue(s) are attached.",
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  await screen.findByText(/Released 23 concept row/);
  expect(screen.queryByRole("heading", { name: "Paused for your decision" }))
    .toBeNull();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();
});

test("a carried semantic issue is read-only and offers no choices", async () => {
  apiMock.getUploadJob.mockImplementation(async (_module: string, id: number) =>
    savedJob({ id, pending_decision: semanticDecisionFixture() }));
  streamMock.mockResolvedValue({ job_id: 42, status: "released", released: true });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  // The issue is reported, with the reference needed to find it in the export.
  expect(await screen.findByText(
    "Semantic issue carried into the release",
  )).toBeDefined();
  expect(screen.getByText("phase33-host-abc123")).toBeDefined();
  // ...and nothing about it is actionable.
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(screen.queryByRole("button", { name: /Save decision/ })).toBeNull();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();
});

test("a stale pending decision never blocks starting a run", async () => {
  // Previously this hid the generate button behind the decision panel, which
  // is how a job could become unstartable without a manual answer.
  apiMock.getUploadJob.mockImplementation(async (_module: string, id: number) =>
    savedJob({ id, pending_decision: semanticDecisionFixture() }));

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));

  expect(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  })).toBeDefined();
});
