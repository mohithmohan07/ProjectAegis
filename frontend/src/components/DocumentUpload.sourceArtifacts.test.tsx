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
      shadow_mode: true,
      used_for_generation: false,
      schema_version: "1.0.0",
      compiler_version: "phase-1-shadow-1",
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

test("shows inspectable Phase 1 artifacts without implying generation cutover", () => {
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

  expect(screen.getByText("Phase 1 canonical-source shadow")).toBeDefined();
  expect(screen.getByText("not used for generation")).toBeDefined();
  expect(screen.getByText(/6 sections · 42 blocks · 26 tasks/i)).toBeDefined();
  expect(screen.getByText(/current pipeline still reads the immutable raw MMD/i))
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
