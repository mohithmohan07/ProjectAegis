import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
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
  resumableConceptCheckpoints: vi.fn(),
  clearConceptCheckpoint: vi.fn(),
  getUploadJob: vi.fn(),
  submitConceptDecision: vi.fn(),
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

function sourceReviewDecisionFixture(
  overrides: Partial<PendingSemanticDecision> = {},
): PendingSemanticDecision {
  return {
    decision_id: "phase3-source-review-def456",
    kind: "phase3_source_graph_review",
    phase: "3",
    conflict:
      "The extracted task points to an orphan figure reference. GPT cannot "
      + "repair the source identity without risking a change to the textbook.",
    diagnosis:
      "The extracted task points to an orphan figure reference. GPT cannot "
      + "repair the source identity without risking a change to the textbook.",
    decision_question:
      "Which verified PDF evidence should replace the unresolved source link?",
    item: {
      unit_id: "BLK-00102",
      type_id: "phase21_orphan_task_figure",
      type_title: "Orphan task figure reference",
      qids: ["Q-0017"],
      questions: ["Study Figure 6 and explain how nationalism spread."],
      topic: "The French Revolution and the Idea of the Nation",
    },
    candidates: [
      {
        target_id: "PDF-0007:BLK-0042",
        concept_id: "PDF-0007:BLK-0042",
        title: "PDF page 7 · figure and caption",
        topic: "The French Revolution and the Idea of the Nation",
        coverage: "Figure 6 and its complete textbook caption.",
        gap: "The converted task lost its parent-section link.",
      },
      {
        target_id: "PDF-0008:BLK-0048",
        concept_id: "PDF-0008:BLK-0048",
        title: "PDF page 8 · following discussion",
        topic: "The French Revolution and the Idea of the Nation",
        coverage: "The paragraph following Figure 6.",
        gap: "It does not include the figure caption.",
      },
    ],
    evidence: [
      {
        page: "6",
        label: "BLK-00102",
        text: "Study Figure 6 and explain how nationalism spread.",
      },
      {
        page: "7",
        label: "PDF-0007:BLK-0042",
        text: "Figure 6 — Nationalist movements in Europe.",
      },
    ],
    options: [
      {
        choice: "accept_recommended",
        label: "Use the recommended verified PDF evidence",
        recommended: true,
        target_id: "PDF-0007:BLK-0042",
        target_concept_id: "PDF-0007:BLK-0042",
      },
      {
        choice: "select_candidate",
        label: "Choose another verified PDF evidence block",
        recommended: false,
      },
      {
        choice: "custom_instruction",
        label: "Tell Aegis what to do",
        recommended: false,
      },
    ],
    cumulative_usage: cumulativeUsage(326731),
    checkpoint_progress: 0.65,
    ...overrides,
  };
}

function workingSourcePatchDecisionFixture(): PendingSemanticDecision {
  const patchHash = "f".repeat(64);
  const patchTarget = `canonical-topic-patch-${patchHash.slice(0, 24)}`;
  const before = [
    "## 1 The French Revolution and the Idea of the Nation",
    "## 3 The Age of Revolutions: 1830-1848",
  ].join("\n");
  const after = [
    "## 1 The French Revolution and the Idea of the Nation",
    "## 2 The Making of Nationalism in Europe",
    "## 3 The Age of Revolutions: 1830-1848",
  ].join("\n");

  return sourceReviewDecisionFixture({
    decision_id: "phase3-source-topic-patch-abc456",
    conflict:
      "Semantic graph omitted or changed numbered main topic 2 The Making "
      + "of Nationalism in Europe.",
    diagnosis:
      "Aegis verified the canonical topic in the source contract and prepared "
      + "a bounded repair for the derived working MMD. The uploaded raw MMD "
      + "remains byte-for-byte unchanged.",
    decision_question:
      "Apply this verified working-source patch, provide different guidance, "
      + "or upload a different source?",
    checkpoint_progress: 0.81,
    item: {
      unit_id: "CANONICAL-TOPIC-0002",
      type_id: "numbered_main_topic_coverage",
      type_title: "Working-source topic patch",
      qids: [],
      questions: ["2 The Making of Nationalism in Europe"],
      topic: "Canonical chapter topic spine",
    },
    candidates: [{
      target_id: patchTarget,
      concept_id: patchTarget,
      title: "Repair the working MMD topic spine",
      topic: "Canonical chapter topic spine",
    }],
    evidence: [
      {
        evidence_id: "CURRENT-WORKING-MMD-TOPIC-SPINE",
        page: "",
        label: "Current working MMD topic spine",
        text: before,
      },
      {
        evidence_id: "VERIFIED-PATCHED-TOPIC-SPINE",
        page: "",
        label: "Verified patched topic spine",
        text: after,
      },
    ],
    options: [
      {
        choice: "accept_recommended",
        label: "Apply the verified working-source patch",
        recommended: true,
        target_id: patchTarget,
      },
      {
        choice: "custom_instruction",
        label: "Tell Aegis how to correct the working source",
        recommended: false,
      },
      {
        choice: "replace_source",
        label: "Upload a different source instead",
        recommended: false,
      },
    ],
    source_patch: {
      version: "phase3-canonical-topic-patch-1",
      kind: "canonical_topic_binding",
      target: "working_derived_source",
      verified: true,
      raw_source_mutated: false,
      source_contract_hash: "a".repeat(64),
      semantic_context_hash: "b".repeat(64),
      before_sha256: "c".repeat(64),
      after_sha256: "d".repeat(64),
      patch_hash: patchHash,
      target_id: patchTarget,
      before,
      after,
      operations: [
        "Restore numbered main topic 2 The Making of Nationalism in Europe "
        + "at canonical section SEC-0009.",
      ],
    },
  });
}

function typeGranularityDecisionFixture(): PendingSemanticDecision {
  return {
    decision_id: "type-granularity-abc123",
    kind: "type_granularity_review",
    phase: "type_mining",
    conflict: "The mined assessment taxonomy may be too fragmented.",
    diagnosis:
      "Aegis found 25 Types for 26 source questions/tasks (96%). The normal "
      + "consolidation pass merged 0.",
    decision_question:
      "Should Aegis keep these distinct Types, or run one bounded "
      + "proposal-and-critic pair?",
    item: {
      unit_id: "",
      type_id: "TYPE-GRANULARITY-REVIEW",
      type_title: "25 Types for 26 QIDs",
      qids: ["QINV-0001", "QINV-0002"],
      questions: [],
      topic: "Chapter-wide Type taxonomy",
    },
    candidates: [],
    evidence: [
      { page: "", label: "Type-to-QID ratio", text: "25/26 (96.2%)" },
      {
        page: "",
        label: "Ordinary consolidation result",
        text: "0 Type(s) merged",
      },
    ],
    options: [
      {
        choice: "consolidate_types",
        label: "Consolidate into fewer reusable Types",
        recommended: true,
      },
      {
        choice: "keep_distinct_types",
        label: "Keep the current distinct Types",
        recommended: false,
      },
      {
        choice: "custom_instruction",
        label: "Specify a grouping rule or target range",
        recommended: false,
      },
    ],
    cumulative_usage: cumulativeUsage(1781587),
  };
}

function topologyDecisionFixture(): PendingSemanticDecision {
  return sourceReviewDecisionFixture({
    decision_id: "phase32-blueprint-abc456",
    kind: "phase32_concept_blueprint_semantic_conflict",
    phase: "3.2",
    decision_question: "How should Aegis repair this concept boundary?",
    candidates: [
      {
        target_id: "3.2:refine:aaa",
        concept_id: "",
        title: "Refine this concept to its verified source claim",
        topic: "The French Revolution and the Idea of the Nation",
        coverage: "Narrow the unsupported clause.",
        gap: "Remove only the unsupported portion.",
      },
      {
        target_id: "3.2:split:bbb",
        concept_id: "",
        title: "Split distinct source-supported concepts",
        topic: "Across verified source topics",
        coverage: "Separate two durable ideas.",
        gap: "Independent criticism remains mandatory.",
      },
    ],
    options: [
      {
        choice: "select_candidate",
        label: "Choose refinement, move, split, or keep",
        recommended: true,
      },
      {
        choice: "custom_instruction",
        label: "Give a custom instruction",
        recommended: false,
      },
    ],
  });
}

function sourceTopicDecisionFixture(): PendingSemanticDecision {
  return sourceReviewDecisionFixture({
    decision_id: "source-topology-abc456",
    kind: "source_topic_coverage_review",
    phase: "concept_topology",
    conflict:
      "The generated concept topology does not preserve every numbered "
      + "main topic in the source.",
    diagnosis:
      "The source contains 6 structurally proven main topics, but the "
      + "current concept map has no normal concept under 1 of them.",
    decision_question:
      "Should Aegis keep every source topic separate and make one bounded "
      + "recovery request, or should the source/topology direction change?",
    checkpoint_progress: 0.35,
    item: {
      unit_id: "",
      type_id: "SOURCE-TOPIC-COVERAGE",
      type_title: "1 missing of 6 source topics",
      qids: [],
      questions: ["The Making of Nationalism in Europe"],
      topic: "Chapter source topology",
    },
    candidates: [{
      target_id: "preserve-all-source-topics",
      concept_id: "",
      title: "Keep every source topic separate",
      topic: "All six numbered main topics",
      coverage: "The Making of Nationalism in Europe",
      gap: "No normal concept currently represents this source topic.",
    }],
    evidence: [{
      page: "",
      label: "Missing source topics",
      text: "The Making of Nationalism in Europe",
    }],
    options: [
      {
        choice: "accept_recommended",
        label: "Keep all source topics separate and recover",
        recommended: true,
        target_id: "preserve-all-source-topics",
      },
      {
        choice: "replace_source",
        label: "Replace or correct the source",
        recommended: false,
      },
      {
        choice: "custom_instruction",
        label: "Specify source-topic recovery guidance",
        recommended: false,
      },
    ],
  });
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
  apiMock.resumableConceptCheckpoints.mockImplementation(
    async (kind: "post" | "pre") => ({
      items: kind === "post" ? [savedSummary()] : [],
      total: kind === "post" ? 1 : 0,
    }),
  );
  apiMock.getUploadJob.mockImplementation(
    async (_module: string, id: number) => savedJob({ id }),
  );
  apiMock.submitConceptDecision.mockResolvedValue({
    status: "decision_recorded",
    resume_required: true,
    resolved_decision: {},
  });
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
  apiMock.resumableConceptCheckpoints.mockImplementation(
    async (kind: "post" | "pre") => ({
      items: kind === "post"
        ? [savedSummary({
          checkpoint_stage: "final_content_ready",
          checkpoint_progress: 0.98,
        })]
        : [],
      total: kind === "post" ? 1 : 0,
    }),
  );
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

test("pauses for a semantic decision and saving it never resumes implicitly", async () => {
  const pendingDecision = semanticDecisionFixture();
  streamMock
    .mockResolvedValueOnce({
      job_id: 42,
      status: "awaiting_decision",
      pending_decision: pendingDecision,
      resume_required: false,
    })
    .mockResolvedValueOnce({
      job_id: 42,
      concept_ids: [],
      inventory_items: 0,
    });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByRole("heading", {
    name: "Paused for your decision",
  })).toBeDefined();
  expect(screen.getByText("No API request is running while paused.")).toBeDefined();
  expect(screen.getByText(/TYPE-0002/)).toBeDefined();
  expect(screen.getByText(
    "Why, in Renan's view, are nations important?",
  )).toBeDefined();
  expect(screen.getByText(
    /Current coverage: Attributes of a nation.*Missing requirement: Importance/,
  )).toBeDefined();
  expect(screen.getByText(/does not call GPT or resume generation/)).toBeDefined();
  expect(screen.getByText(/1991461 tokens/)).toBeDefined();

  fireEvent.click(screen.getByRole("radio", {
    name: /Expand the existing concept/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "phase33-host-abc123",
      {
        choice: "expand_existing",
        target_concept_id: "HOST-CONCEPT-0001",
      },
    );
  });
  expect(streamMock).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Decision saved.")).toBeDefined();

  fireEvent.click(screen.getByRole("button", {
    name: "Resume generation",
  }));
  await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(2));
});

test("shows the saved reason when one bounded autonomous review escalates", async () => {
  streamMock.mockResolvedValueOnce({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: semanticDecisionFixture({
      agent_review: {
        status: "escalated",
        resolver_version: "aegis-autonomous-resolution-v1",
        issue_key: "a".repeat(64),
        started_at: "2026-08-01T10:00:00Z",
        completed_at: "2026-08-01T10:00:04Z",
        reason: "Two source-grounded concept hosts remain equally defensible.",
        confidence: 0.71,
        evidence_refs: ["MMD-WINDOW-0002", "PENDING-EVIDENCE-0001"],
        choice: null,
        instruction: "",
        target_id: "",
        target_concept_id: "",
      },
    }),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText("Aegis autonomous review")).toBeDefined();
  expect(screen.getByText("Saved status: Human judgment needed")).toBeDefined();
  expect(screen.getByText(
    /tried one bounded autonomous review.*will not run another autonomous review/s,
  )).toBeDefined();
  expect(screen.getByText("Saved reason:")).toBeDefined();
  expect(screen.getByText(
    "Two source-grounded concept hosts remain equally defensible.",
  )).toBeDefined();
  expect(screen.getByText("No API request is running while paused.")).toBeDefined();
  expect(streamMock).toHaveBeenCalledTimes(1);
});

test("explains that a recorded autonomous attempt will not be repeated", async () => {
  streamMock.mockResolvedValueOnce({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: semanticDecisionFixture({
      agent_review: {
        status: "request_started",
        resolver_version: "aegis-autonomous-resolution-v1",
        issue_key: "b".repeat(64),
        started_at: "2026-08-01T10:00:00Z",
        completed_at: "",
        reason: "The prior request has no safely persisted final directive.",
        confidence: 0,
        evidence_refs: [],
        choice: null,
        instruction: "",
        target_id: "",
        target_concept_id: "",
        offered_candidate_count: 3,
        inspected_candidate_count: 0,
      },
    }),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText("Saved status: Attempt recorded")).toBeDefined();
  expect(screen.getByText(
    /avoid a duplicate paid request.*will not repeat the review/s,
  )).toBeDefined();
  expect(screen.getByText(
    /3 sealed candidates catalogued; detail inspection outcome unknown/,
  )).toBeDefined();
  expect(screen.queryByText(/0 expanded with source detail/)).toBeNull();
  expect(streamMock).toHaveBeenCalledTimes(1);
});

test("grounding escalation shows the bounded search and exact candidate bindings", async () => {
  const textHash = "1".repeat(64);
  const bindingHash = "2".repeat(64);
  streamMock.mockResolvedValueOnce({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: sourceReviewDecisionFixture({
      decision_id: "phase31-grounding-bound-candidates",
      kind: "phase31_source_grounding_semantic_conflict",
      candidates: [
        {
          target_id: "3.1:evidence:cavour",
          concept_id: "",
          title: "Use verified evidence BLK-00249",
          topic: "The Making of Nationalism in Europe",
          coverage:
            "Through a tactful diplomatic alliance with France engineered by Cavour.",
          gap: "",
          action: "use_verified_evidence",
          source_block_ids: ["BLK-00249"],
          source_topic_id: "TOPIC-0002",
          source_kind: "paragraph",
          source_page: 15,
          text_sha256: textHash,
          binding_hash: bindingHash,
        },
        {
          target_id: "3.1:move:revolutions",
          concept_id: "CONCEPT-0017",
          title: "Move the complete claim to a different source topic",
          topic: "The Age of Revolutions: 1830–1848",
          coverage: "Move without changing the claim.",
          gap: "Independent review remains mandatory.",
          action: "move",
          source_topic_id: "TOPIC-0002",
          target_topic_id: "TOPIC-0003",
          boundary_relation: "cross_topic",
        },
        {
          target_id: "3.1:move:nationalism",
          concept_id: "CONCEPT-0017",
          title: "Move the complete claim to a different source topic",
          topic: "The Making of Nationalism in Europe",
          coverage: "Move without changing the claim.",
          gap: "Independent review remains mandatory.",
          action: "move",
          source_topic_id: "TOPIC-0003",
          target_topic_id: "TOPIC-0002",
          boundary_relation: "cross_topic",
        },
      ],
      options: [{
        choice: "select_candidate",
        label: "Select evidence or a topology repair",
        recommended: true,
      }],
      agent_review: {
        status: "escalated",
        resolver_version: "aegis-autonomous-resolution-v2",
        issue_key: "3".repeat(64),
        started_at: "2026-08-01T10:00:00Z",
        completed_at: "2026-08-01T10:00:04Z",
        reason: "No single action passed every deterministic source gate.",
        confidence: 0.74,
        evidence_refs: ["BLK-00249", "MMD-WINDOW-0002"],
        choice: null,
        instruction: "",
        target_id: "",
        target_concept_id: "",
        offered_candidate_count: 3,
        inspected_candidate_count: 1,
      },
    }),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText(/searched the saved source and candidate packet once/))
    .toBeDefined();
  expect(screen.getByText(
    /one saved search over 3 sealed candidates catalogued; 1 expanded with source detail/,
  ))
    .toBeDefined();
  expect(screen.getByText(/does not run the resolver or make another API request/))
    .toBeDefined();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();
  expect(streamMock).toHaveBeenCalledTimes(1);
  expect(screen.getAllByRole("radio").every((radio) =>
    !(radio as HTMLInputElement).checked)).toBe(true);

  const evidenceGroup = screen.getByRole("region", {
    name: "Bound source candidates",
  });
  expect(within(evidenceGroup).getByText("BLK-00249")).toBeDefined();
  expect(within(evidenceGroup).getByText("15")).toBeDefined();
  expect(evidenceGroup.textContent).toContain("TOPIC-0002");
  expect(within(evidenceGroup).getByText(
    "Through a tactful diplomatic alliance with France engineered by Cavour.",
  )).toBeDefined();
  expect(within(evidenceGroup).getByText(textHash)).toBeDefined();
  expect(within(evidenceGroup).getByText(bindingHash)).toBeDefined();
  expect(screen.getByRole("region", { name: "Topology repairs" }))
    .toBeDefined();

  fireEvent.click(screen.getByRole("radio", {
    name: /Select evidence or a topology repair/,
  }));
  const target = screen.getByRole("combobox", {
    name: "Evidence or topology repair",
  });
  expect(within(target).getByRole("option", {
    name: /Move the complete claim.*→ The Age of Revolutions: 1830–1848/,
  })).toBeDefined();
  expect(within(target).getByRole("option", {
    name: /Move the complete claim.*→ The Making of Nationalism in Europe/,
  })).toBeDefined();
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

test("Type granularity pause offers quality-safe consolidation or keep choices", async () => {
  streamMock.mockResolvedValueOnce({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: typeGranularityDecisionFixture(),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText(/25 Types for 26 source questions/))
    .toBeDefined();
  expect(screen.getByText("Aegis quality check")).toBeDefined();
  expect(screen.getByText(/deterministic Type-fragmentation risk/))
    .toBeDefined();
  expect(screen.getByText(/one bounded GPT proposal plus an independent critic/))
    .toBeDefined();
  expect(screen.getByText(/exact QID coverage, source wording, topic/))
    .toBeDefined();
  expect(screen.getAllByRole("radio").every((radio) =>
    !(radio as HTMLInputElement).checked)).toBe(true);

  fireEvent.click(screen.getByRole("radio", {
    name: /Consolidate into fewer reusable Types/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "type-granularity-abc123",
      { choice: "consolidate_types" },
    );
  });
  expect(streamMock).toHaveBeenCalledTimes(1);
});

test("a replacement post-learning job clears a stale pending decision", async () => {
  apiMock.getUploadJob.mockResolvedValue(savedJob({
    pending_decision: semanticDecisionFixture(),
  }));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(await screen.findByRole("heading", {
    name: "Paused for your decision",
  })).toBeDefined();

  fireEvent.click(screen.getByRole("button", {
    name: "Replace post upload",
  }));

  await waitFor(() => {
    expect(screen.queryByRole("heading", {
      name: "Paused for your decision",
    })).toBeNull();
  });
  expect(screen.getByText("Loaded post-replacement.pdf")).toBeDefined();
});

test("discarding a checkpoint clears its stale pending decision", async () => {
  apiMock.getUploadJob.mockResolvedValue(savedJob({
    pending_decision: semanticDecisionFixture(),
  }));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(await screen.findByRole("heading", {
    name: "Paused for your decision",
  })).toBeDefined();

  fireEvent.click(screen.getByRole("button", {
    name: "Discard post checkpoint",
  }));

  await waitFor(() => {
    expect(screen.queryByRole("heading", {
      name: "Paused for your decision",
    })).toBeNull();
  });
});

test("starting over in pre-learning clears a stale pending decision", async () => {
  apiMock.resumableConceptCheckpoints.mockImplementation(
    async (kind: "post" | "pre") => ({
      items: kind === "pre"
        ? [savedSummary({ learning_kind: "pre" })]
        : [],
      total: kind === "pre" ? 1 : 0,
    }),
  );
  apiMock.getUploadJob.mockResolvedValue(savedJob({
    learning_kind: "pre",
    pending_decision: semanticDecisionFixture(),
  }));
  renderPage();

  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  expect(await screen.findByRole("heading", {
    name: "Paused for your decision",
  })).toBeDefined();

  fireEvent.click(screen.getByRole("button", {
    name: "Clear pre upload",
  }));

  await waitFor(() => {
    expect(screen.queryByRole("heading", {
      name: "Paused for your decision",
    })).toBeNull();
  });
  expect(screen.getByText("No saved upload")).toBeDefined();
});

test("expand existing requires an explicit target when backend supplied none", async () => {
  const pendingDecision = semanticDecisionFixture({
    options: semanticDecisionFixture().options.map((option) =>
      option.choice === "expand_existing"
        ? { ...option, target_concept_id: undefined }
        : option),
  });
  streamMock.mockResolvedValue({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: pendingDecision,
    resume_required: false,
  });
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  fireEvent.click(await screen.findByRole("radio", {
    name: /Expand the existing concept/,
  }));
  const target = await screen.findByRole("combobox", {
    name: "Concept to expand",
  });
  expect(screen.queryByText("Recommended")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
  expect(await screen.findByText(
    "Select the existing concept Aegis should expand.",
  )).toBeDefined();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();

  fireEvent.change(target, { target: { value: "HOST-CONCEPT-0007" } });
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "phase33-host-abc123",
      {
        choice: "expand_existing",
        target_concept_id: "HOST-CONCEPT-0007",
      },
    );
  });
});

test("Phase 3 source review shows GPT context and requires an explicit safe action", async () => {
  const pendingDecision = sourceReviewDecisionFixture();
  streamMock
    .mockResolvedValueOnce({
      job_id: 42,
      status: "awaiting_decision",
      pending_decision: pendingDecision,
      resume_required: false,
    })
    .mockResolvedValueOnce({
      job_id: 42,
      concept_ids: [],
      inventory_items: 0,
    });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByText("GPT diagnosis")).toBeDefined();
  expect(screen.getByText(
    /cannot repair the source identity without risking a change/,
  )).toBeDefined();
  expect(screen.getByText(
    "Which verified PDF evidence should replace the unresolved source link?",
  )).toBeDefined();
  expect(screen.getByText(
    /Verified source text: Figure 6 and its complete textbook caption/,
  )).toBeDefined();
  expect(screen.getByText(
    /Diagnostic issue: The converted task lost its parent-section link/,
  )).toBeDefined();
  expect(screen.getByText(
    "PDF-0007:BLK-0042 · page 7: Figure 6 — Nationalist movements in Europe.",
  )).toBeDefined();
  expect(screen.getByText(/326731 tokens/)).toBeDefined();
  expect(screen.getByText(
    "No action is selected automatically. Choose one option below.",
  )).toBeDefined();
  expect(screen.getAllByRole("radio").every((radio) =>
    !(radio as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByRole("button", { name: "Save decision" }))
    .toHaveProperty("disabled", true);

  fireEvent.click(screen.getByRole("radio", {
    name: /Use the recommended verified PDF evidence/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "phase3-source-review-def456",
      {
        choice: "accept_recommended",
        target_id: "PDF-0007:BLK-0042",
      },
    );
  });
  expect(streamMock).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Decision saved.")).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "Resume generation" }));
  await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(2));
});

test("numbered-topic failures offer a verified working-MMD patch without mutating the raw source", async () => {
  const pendingDecision = workingSourcePatchDecisionFixture();
  const patchTarget = pendingDecision.source_patch!.target_id;
  streamMock
    .mockResolvedValueOnce({
      job_id: 42,
      status: "awaiting_decision",
      pending_decision: pendingDecision,
      resume_required: false,
    })
    .mockResolvedValueOnce({
      job_id: 42,
      concept_ids: [],
      inventory_items: 0,
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
    "Working-source integrity diagnosis",
  )).toBeDefined();
  expect(screen.getByRole("heading", {
    name: "Affected numbered main topic",
  })).toBeDefined();

  const patchReview = screen.getByRole("region", {
    name: "Verified working-source patch",
  });
  expect(within(patchReview).getByText(
    "Verified derived-source patch",
  )).toBeDefined();
  expect(within(patchReview).getByText("Raw MMD unchanged")).toBeDefined();
  expect(within(patchReview).getByText(
    /uploaded raw MMD remains byte-for-byte unchanged/i,
  )).toBeDefined();
  expect(within(patchReview).getByText(
    "Before · current derived working MMD",
  )).toBeDefined();
  expect(within(patchReview).getByText(
    "After · verified derived working MMD",
  )).toBeDefined();
  expect(patchReview.textContent).toContain(
    "## 2 The Making of Nationalism in Europe",
  );
  expect(within(patchReview).getByText(
    /Restore numbered main topic 2 The Making of Nationalism in Europe/,
  )).toBeDefined();
  expect(screen.queryByText("Verified source candidates")).toBeNull();
  expect(screen.getAllByRole("radio").every((radio) =>
    !(radio as HTMLInputElement).checked)).toBe(true);

  expect(screen.getByRole("radio", {
    name: /Apply the verified working-source patch/,
  })).toBeDefined();
  expect(screen.getByRole("radio", {
    name: /Tell Aegis how to correct the working source/,
  })).toBeDefined();
  expect(screen.getByRole("radio", {
    name: /Upload a different source instead/,
  })).toBeDefined();
  expect(screen.getByText(
    /exact verified topic-spine repair shown above/,
  )).toBeDefined();

  fireEvent.click(screen.getByRole("radio", {
    name: /Apply the verified working-source patch/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "phase3-source-topic-patch-abc456",
      {
        choice: "accept_recommended",
        target_id: patchTarget,
      },
    );
  });
  expect(streamMock).toHaveBeenCalledTimes(1);
  expect(await screen.findByText(
    /applied only to the derived working MMD when you explicitly resume/i,
  )).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "Resume generation" }));
  await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(2));
});

test("source review never guesses and submits only the candidate the user selects", async () => {
  streamMock.mockResolvedValue({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: sourceReviewDecisionFixture(),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  fireEvent.click(await screen.findByRole("radio", {
    name: /Choose another verified PDF evidence block/,
  }));
  const target = screen.getByRole("combobox", {
    name: "Verified source evidence",
  }) as HTMLSelectElement;
  expect(target.value).toBe("");

  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
  expect(await screen.findByText(
    "Select the verified source evidence Aegis should use.",
  )).toBeDefined();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();

  fireEvent.change(target, {
    target: { value: "PDF-0008:BLK-0048" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "phase3-source-review-def456",
      {
        choice: "select_candidate",
        target_id: "PDF-0008:BLK-0048",
      },
    );
  });
  expect(streamMock).toHaveBeenCalledTimes(1);
});

test("topology decisions label actions without calling them source evidence", async () => {
  streamMock.mockResolvedValue({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: topologyDecisionFixture(),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  fireEvent.click(await screen.findByRole("radio", {
    name: /Choose refinement, move, split, or keep/,
  }));
  const target = screen.getByRole("combobox", {
    name: "Source-supported concept action",
  });
  expect(target).toBeDefined();
  expect(screen.getByText(
    /Choose refine, move, split, keep, or retire/,
  )).toBeDefined();
  expect(screen.queryByRole("combobox", {
    name: "Verified source evidence",
  })).toBeNull();
});

test("missing source-topic decisions use topology wording and one-click recovery", async () => {
  streamMock.mockResolvedValue({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: sourceTopicDecisionFixture(),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByRole("heading", {
    name: "Missing source topic",
  })).toBeDefined();
  expect(screen.getByRole("heading", {
    name: "Source-topic recovery plan",
  })).toBeDefined();
  expect(screen.getByText("Source-topic integrity diagnosis")).toBeDefined();
  expect(screen.getByText(
    /Preserve every numbered main topic and authorize one bounded recovery request/,
  )).toBeDefined();
  expect(screen.queryByText("Verified source candidates")).toBeNull();

  fireEvent.click(screen.getByRole("radio", {
    name: /Keep all source topics separate and recover/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "source-topology-abc456",
      {
        choice: "accept_recommended",
        target_id: "preserve-all-source-topics",
      },
    );
  });
});

test("source review safely stops for source replacement after choices are exhausted", async () => {
  streamMock.mockResolvedValue({
    job_id: 42,
    status: "awaiting_decision",
    pending_decision: sourceReviewDecisionFixture({
      candidates: [],
      options: [{
        choice: "replace_source",
        label: "Replace or correct the source file",
        recommended: false,
      }],
      conflict:
        "The previous custom instruction could not be grounded in the source.",
      diagnosis:
        "The previous custom instruction could not be grounded in the source.",
      decision_question:
        "Can you correct or replace the disputed source before continuing?",
    }),
    resume_required: false,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));
  fireEvent.click(await screen.findByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", {
    name: "Resume from 91% checkpoint",
  }));

  expect(await screen.findByRole("radio", {
    name: /Replace or correct the source file/,
  })).toHaveProperty("checked", false);
  expect(screen.getByText(/Aegis will not change it automatically/))
    .toBeDefined();
  fireEvent.click(screen.getByRole("radio", {
    name: /Replace or correct the source file/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));

  await waitFor(() => {
    expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
      42,
      "phase3-source-review-def456",
      { choice: "replace_source" },
    );
  });
  expect(streamMock).toHaveBeenCalledTimes(1);
  expect(await screen.findByText(
    /Replace or correct the source file using the upload controls above/,
  )).toBeDefined();
  expect(screen.queryByRole("button", { name: "Resume generation" })).toBeNull();
});

test("restores the same source-review decision flow for Pre Learning", async () => {
  const pendingDecision = sourceReviewDecisionFixture();
  apiMock.resumableConceptCheckpoints.mockImplementation(
    async (kind: "post" | "pre") => ({
      items: kind === "pre"
        ? [savedSummary({ learning_kind: "pre" })]
        : [],
      total: kind === "pre" ? 1 : 0,
    }),
  );
  apiMock.getUploadJob.mockResolvedValue(savedJob({
    learning_kind: "pre",
    pending_decision: pendingDecision,
  }));
  streamMock.mockResolvedValue({
    job_id: 42,
    concept_ids: [],
    inventory_items: 0,
  });

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Resume" }));

  expect(await screen.findByText("GPT diagnosis")).toBeDefined();
  expect(screen.getByText("Loaded electricity.pdf")).toBeDefined();
  expect(streamMock).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("radio", {
    name: /Tell Aegis what to do/,
  }));
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
  expect(await screen.findByText(
    "Write the instruction Aegis should follow.",
  )).toBeDefined();
  expect(apiMock.submitConceptDecision).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", {
    name: "Custom instruction",
  }), {
    target: {
      value:
        "Keep the task under Section 2 and use page 7 only as its figure evidence.",
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save decision" }));
  expect(await screen.findByText("Decision saved.")).toBeDefined();
  expect(apiMock.submitConceptDecision).toHaveBeenCalledWith(
    42,
    "phase3-source-review-def456",
    {
      choice: "custom_instruction",
      instruction:
        "Keep the task under Section 2 and use page 7 only as its figure evidence.",
    },
  );
  expect(screen.getByRole("button", { name: "Resume generation" }))
    .toHaveProperty("disabled", true);
  expect(streamMock).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", {
    name: "Select Electricity target",
  }));
  fireEvent.click(screen.getByRole("button", { name: "Resume generation" }));

  await waitFor(() => expect(streamMock).toHaveBeenCalledTimes(1));
  expect(streamMock.mock.calls[0][0]).toBe("/pre/42");
});
