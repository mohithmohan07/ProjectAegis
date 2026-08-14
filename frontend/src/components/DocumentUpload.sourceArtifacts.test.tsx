import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import type { UploadJob } from "../types";
import DocumentUpload from "./DocumentUpload";

const streamNdjsonMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  getUploadJob: vi.fn(),
  checkpointUrl: vi.fn((id: number) => `/checkpoint/${id}`),
  clearConceptCheckpoint: vi.fn(),
  importConceptCheckpoint: vi.fn(),
  paths: {
    assessmentConvert: vi.fn((id: number) => `/assessments/${id}/convert`),
    conceptConvert: vi.fn((id: number) => `/concepts/${id}/convert`),
  },
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: streamNdjsonMock,
}));

function convertedJob(): UploadJob {
  return {
    id: 81,
    module: "build_concepts",
    upload_type: "document",
    textbook_mode: "",
    learning_kind: "post",
    filename: "ordered-source.mmd",
    mmd_text: "# Ordered source",
    deposit_scope_type: "chapter",
    deposit_scope_ids: [],
    status: "converted",
    result_ids: [],
    detail: "Converted to MMD",
    source_artifacts: {
      available: true,
      shadow_mode: false,
      used_for_generation: true,
      schema_version: "1.1.0",
      compiler_version: "phase-2-source-critical-1",
      phase: "phase-2-source-critical",
      consumer_module: "build_concepts",
      generation_usage: {
        mode: "source-critical",
        components: ["question_task_inventory", "stable_qids"],
        raw_mmd_components: ["semantic_concept_extraction"],
      },
      phase2_inventory_ready: true,
      status: "passed_with_warnings",
      ready_for_future_cutover: false,
      source_sha256: "abc123",
      manifest_url: "/source-artifacts/uploads/81",
      summary: {
        sections: 6,
        blocks: 42,
        tasks: 26,
        images: 19,
        math_spans: 4,
        errors: 0,
        warnings: 2,
      },
      files: [
        {
          kind: "raw_mmd",
          label: "Immutable raw MMD",
          filename: "source.raw.mmd",
          media_type: "text/markdown; charset=utf-8",
          size_bytes: 60648,
          download_url: "/source-artifacts/uploads/81/raw_mmd",
        },
        {
          kind: "canonical_json",
          label: "Aegis canonical source JSON",
          filename: "source.aegis-source.json",
          media_type: "application/json; charset=utf-8",
          size_bytes: 120000,
          download_url: "/source-artifacts/uploads/81/canonical_json",
        },
        {
          kind: "aegis_mmd",
          label: "Derived Aegis MMD",
          filename: "source.aegis.mmd",
          media_type: "text/markdown; charset=utf-8",
          size_bytes: 70000,
          download_url: "/source-artifacts/uploads/81/aegis_mmd",
        },
        {
          kind: "report",
          label: "Source validation report",
          filename: "source.source-report.json",
          media_type: "application/json; charset=utf-8",
          size_bytes: 4000,
          download_url: "/source-artifacts/uploads/81/report",
        },
      ],
    },
    created_at: "2026-07-28T04:00:00Z",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getUploadJob.mockRejectedValue(new Error("no saved job"));
});

test("shows the Phase 2 source-critical cutover without overstating semantic use", () => {
  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={convertedJob()}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  expect(screen.getByText("Phase 2 canonical-source inventory")).toBeDefined();
  expect(screen.getByText("source-critical generation active")).toBeDefined();
  expect(screen.getByText(/6 sections · 42 blocks · 26 tasks/i)).toBeDefined();
  expect(screen.getByText(/semantic concept extraction and writing still read/i))
    .toBeDefined();

  const raw = screen.getByRole("link", { name: "Immutable raw MMD" });
  expect(raw.getAttribute("href")).toBe(
    "/source-artifacts/uploads/81/raw_mmd",
  );
  expect(screen.getByRole("link", { name: "Aegis canonical source JSON" }))
    .toBeDefined();
  expect(screen.getByRole("link", { name: "Derived Aegis MMD" }))
    .toBeDefined();
  expect(screen.getByRole("link", { name: "Source validation report" }))
    .toBeDefined();
});

test("shows bounded Phase 2.2 source adjudication before generation", () => {
  const job = convertedJob();
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.phase2_inventory_ready = false;
  job.source_artifacts.status = "failed";
  job.source_artifacts.source_adjudication = {
    version: "2.2.1",
    status: "pending",
    packet_count: 2,
    eligible_issue_count: 3,
  };

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={job}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  expect(screen.getByText("Phase 2.2.1 canonical-source review")).toBeDefined();
  expect(screen.getByText("AI source adjudication pending")).toBeDefined();
  expect(screen.getByText("awaiting source adjudication")).toBeDefined();
  expect(screen.getByText(/only the relevant original-document pages/i))
    .toBeDefined();
});

test("shows verified Phase 2.2 overlays without calling them raw-MMD edits", () => {
  const job = convertedJob();
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.phase = "phase-2.2-source-adjudicated";
  job.source_artifacts.source_adjudication = {
    version: "2.2.1",
    status: "verified",
    verified_repairs: 2,
    remaining_issues: 0,
  };

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={job}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  expect(screen.getByText("Phase 2.2.1 canonical-source inventory")).toBeDefined();
  expect(screen.getByText("source adjudication verified")).toBeDefined();
  expect(screen.getByText(/immutable raw MMD remains available unchanged/i))
    .toBeDefined();
});


test("shows verified GPT PDF-to-ACSD provenance and artifacts", () => {
  const job = convertedJob();
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.phase = "phase-2.2-source-adjudicated";
  job.source_artifacts.source_reconstruction = {
    version: "2.2.1",
    status: "verified",
    source_origin: "gpt_pdf_acsd_fallback",
    page_count: 12,
    batch_count: 4,
    asset_count: 7,
  };
  job.source_artifacts.files.push({
    kind: "gpt_page_acsd",
    label: "GPT page-level ACSD extraction",
    filename: "source.gpt-page-acsd.json",
    media_type: "application/json; charset=utf-8",
    size_bytes: 8000,
    download_url: "/source-artifacts/uploads/81/gpt_page_acsd",
  });

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={job}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  expect(screen.getByText("Phase 2.2.1 GPT-reconstructed canonical source"))
    .toBeDefined();
  expect(screen.getByText("GPT PDF-to-ACSD")).toBeDefined();
  expect(screen.getByText(/read 12 PDF page\(s\)/i)).toBeDefined();
  expect(screen.getByRole("link", { name: "GPT page-level ACSD extraction" }))
    .toBeDefined();
});


test("shows GPT PDF-to-ACSD review-required state without presenting it as pending adjudication", () => {
  const job = convertedJob();
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.status = "failed";
  job.source_artifacts.phase2_inventory_ready = false;
  job.source_artifacts.source_reconstruction = {
    version: "2.2.1",
    status: "review_required",
    source_origin: "gpt_pdf_acsd_fallback",
    fallback_reason: ["pdf_text_coverage_too_low:0.120"],
  };

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={job}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  expect(screen.getByText("Phase 2.2.1 GPT source reconstruction review"))
    .toBeDefined();
  expect(screen.getByText("source reconstruction review required")).toBeDefined();
  expect(screen.getByText("GPT PDF-to-ACSD review required")).toBeDefined();
  expect(screen.getByText(/concept generation is blocked/i)).toBeDefined();
});
