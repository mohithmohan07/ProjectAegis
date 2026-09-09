import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import type { UploadJob } from "../types";
import ConceptReviewWorkflow, { isConceptReviewWaiting } from "./ConceptReviewWorkflow";

const apiMock = vi.hoisted(() => ({
  conceptFileUrl: vi.fn((id: number, lane: string) => `/concepts/${id}?lane=${lane}`),
  uploadCorrectedConceptInput: vi.fn(),
  uploadEditedWorkbook: vi.fn(),
  getUploadJob: vi.fn(),
  paths: { masterGenerate: vi.fn((id: number) => `/master/${id}`) },
}));
const streamMock = vi.hoisted(() => vi.fn());

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: streamMock,
  isNonTransientStatus: () => false,
}));

function job(status: "pending_review" | "reviewed" | "master_building" | "master_ready" = "pending_review"): UploadJob {
  return {
    id: 55,
    module: "build_concepts",
    upload_type: "document",
    textbook_mode: "",
    learning_kind: "post",
    filename: "chapter.pdf",
    mmd_text: "# Chapter",
    deposit_scope_type: "chapter",
    deposit_scope_ids: [11],
    status: status === "master_ready" ? "generated" : "released",
    result_ids: [],
    detail: "",
    source_artifacts: {
      available: true,
      shadow_mode: false,
      used_for_generation: true,
      schema_version: "1",
      compiler_version: "1",
      status: "passed",
      ready_for_future_cutover: false,
      source_sha256: "abc",
      manifest_url: "/manifest",
      summary: {},
      files: [
        {
          kind: "release_bulk_import",
          label: "Post Concept",
          filename: "post.xlsx",
          media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size_bytes: 10,
          download_url: "/post.xlsx",
        },
        {
          kind: "pre_release_bulk_import",
          label: "Pre Concept",
          filename: "pre.xlsx",
          media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size_bytes: 10,
          download_url: "/pre.xlsx",
        },
      ],
    },
    review_workflow: {
      status,
      corrected_inputs: {},
    },
    created_at: "2026-09-09T00:00:00Z",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getUploadJob.mockResolvedValue(job("reviewed"));
  apiMock.uploadCorrectedConceptInput.mockResolvedValue({
    review_workflow: { status: "reviewed", corrected_inputs: {
      post: { lane: "post", filename: "post-edited.xlsx", status: "accepted" },
    } },
  });
  streamMock.mockResolvedValue({ status: "master_ready" });
});

test("waits at pending review with distinct downloads and correction inputs", () => {
  const current = job();
  expect(isConceptReviewWaiting(current)).toBe(true);
  render(
    <RunConsoleProvider>
      <ConceptReviewWorkflow job={current} onJob={vi.fn()} />
    </RunConsoleProvider>,
  );

  expect(screen.getByRole("link", { name: "Download the Post-Learning Concept File" })).toBeDefined();
  expect(screen.getByRole("link", { name: "Download the Pre-Learning Concept File" })).toBeDefined();
  expect(screen.getByTestId("corrected-input-post")).toBeDefined();
  expect(screen.getByTestId("corrected-input-pre")).toBeDefined();
  expect(screen.getByRole("button", { name: "Generate Master Files" })).toBeDefined();
  expect(screen.queryByText(/upload.*CMS/i)).toBeNull();
});

test("uploads the same edited Concept workbook without publishing", async () => {
  const onJob = vi.fn();
  const current = job();
  render(
    <RunConsoleProvider>
      <ConceptReviewWorkflow job={current} onJob={onJob} />
    </RunConsoleProvider>,
  );

  fireEvent.change(screen.getByTestId("corrected-input-post"), {
    target: { files: [new File(["xlsx"], "post-edited.xlsx")] },
  });
  await waitFor(() => expect(apiMock.uploadCorrectedConceptInput).toHaveBeenCalledWith(
    55,
    "post",
    expect.any(File),
  ));
  expect(apiMock.uploadEditedWorkbook).not.toHaveBeenCalled();
  expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ id: 55 }));
  expect((await screen.findByTestId("accepted-input-post")).textContent)
    .toContain("Accepted file: post-edited.xlsx");
});

test("only the explicit Generate Master action starts the second run", async () => {
  const onJob = vi.fn();
  const current = job("reviewed");
  render(
    <RunConsoleProvider>
      <ConceptReviewWorkflow job={current} onJob={onJob} />
    </RunConsoleProvider>,
  );

  expect(streamMock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("generate-master"));
  await waitFor(() => expect(streamMock).toHaveBeenCalled());
  expect(apiMock.paths.masterGenerate).toHaveBeenCalledWith(55);
  expect(apiMock.getUploadJob).toHaveBeenCalledWith("concepts", 55);
});

test("master_ready exits the review boundary for the historical output surface", () => {
  expect(isConceptReviewWaiting(job("master_ready"))).toBe(false);
});
