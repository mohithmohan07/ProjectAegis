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
