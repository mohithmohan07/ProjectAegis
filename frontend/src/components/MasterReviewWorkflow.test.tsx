import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import type { SourceArtifactFile, UploadJob } from "../types";
import MasterReviewWorkflow, {
  conceptPublicationState,
  isMasterReviewStage,
} from "./MasterReviewWorkflow";

const apiMock = vi.hoisted(() => ({
  conceptFileUrl: vi.fn((id: number, lane: string) => `/concepts/${id}?lane=${lane}`),
  uploadReviewedMaster: vi.fn(),
  publishReviewedMaster: vi.fn(),
  uploadConceptRelease: vi.fn(),
  uploadEditedWorkbook: vi.fn(),
  getUploadJob: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  streamNdjson: vi.fn(),
  isNonTransientStatus: () => false,
}));

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function files(options: { conceptPublished?: Partial<Record<"post" | "pre", boolean>> } = {}): SourceArtifactFile[] {
  const published = options.conceptPublished ?? {};
  return [
    {
      kind: "release_bulk_import",
      label: "Download the Post-Learning Concept File",
      filename: "post_concepts.xlsx",
      media_type: XLSX,
      size_bytes: 10,
      download_url: "/build-concepts/uploads/55/release-bulk-import.xlsx",
      action: "download",
    },
    {
      kind: "release_master",
      label: "Download the Post-Learning Master File",
      filename: "post_master.xlsx",
      media_type: XLSX,
      size_bytes: 20,
      download_url: "/build-assessments/releases/22/master.xlsx",
      action: "download",
    },
    {
      kind: "pre_release_bulk_import",
      label: "Download the Pre-Learning Concept File",
      filename: "pre_concepts.xlsx",
      media_type: XLSX,
      size_bytes: 10,
      download_url: "/build-concepts/uploads/55/release-bulk-import.xlsx?lane=pre",
      action: "download",
    },
    {
      kind: "pre_release_master",
      label: "Pre-Learning Master File",
      filename: "",
      media_type: XLSX,
      size_bytes: 0,
      download_url: "",
      action: "download",
      disabled: true,
      disabled_reason: "The 'pre' lane's Master File was not built: OSError: [Errno 28] No space left on device.",
    },
    {
      kind: "database_upload",
      label: published.post ? "Already uploaded to database" : "Upload released output to database",
      filename: "",
      media_type: "application/json",
      size_bytes: 0,
      download_url: "/build-concepts/uploads/55/upload-release?lane=post",
      action: "post",
      disabled: Boolean(published.post),
      requires_confirmation: true,
      release_state: "ready",
    },
    {
      kind: "pre_database_upload",
      label: published.pre ? "Already uploaded to database" : "Upload released Pre-Learning output to database",
      filename: "",
      media_type: "application/json",
      size_bytes: 0,
      download_url: "/build-concepts/uploads/55/upload-release?lane=pre",
      action: "post",
      disabled: Boolean(published.pre),
      requires_confirmation: true,
      release_state: "ready",
    },
  ];
}

function job(overrides: Partial<UploadJob> = {}, fileOptions: Parameters<typeof files>[0] = {}): UploadJob {
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
    status: "generated",
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
      files: files(fileOptions),
    },
    review_workflow: { status: "master_ready", corrected_inputs: {}, master_review: {} },
    created_at: "2026-09-11T00:00:00Z",
    ...overrides,
  };
}

function spyOnConfirm() {
  return vi.spyOn(window, "confirm");
}
let confirmSpy: ReturnType<typeof spyOnConfirm>;

beforeEach(() => {
  vi.clearAllMocks();
  confirmSpy = spyOnConfirm().mockImplementation(() => true);
  apiMock.getUploadJob.mockResolvedValue(job());
  apiMock.uploadReviewedMaster.mockResolvedValue({
    lane: "post",
    filename: "post-master-reviewed.xlsx",
    release_id: 22,
    release_uid: "REL-22",
    version: 2,
    round_recorded: true,
    changed_fields: 3,
    omitted_questions: 1,
    added_questions: 0,
    readiness: "ready_with_flags",
    issues: ["Q-0004 keyword cell is blank"],
    master_review: {
      filename: "post-master-reviewed.xlsx",
      uploaded_at: "2026-09-11T10:00:00Z",
      release_id: 22,
      release_uid: "REL-22",
      version: 2,
      changed_fields: 3,
      omitted: 1,
      added: 0,
      readiness: "ready_with_flags",
      issues: ["Q-0004 keyword cell is blank"],
      status: "accepted",
    },
    review_workflow: { status: "master_ready" },
  });
  apiMock.uploadConceptRelease.mockResolvedValue({ status: "generated" });
  apiMock.publishReviewedMaster.mockResolvedValue({
    lane: "post",
    release_id: 22,
    release_uid: "REL-22",
    version: 2,
    database: { groups_created: 1, questions_created: 3, labels_reissued: 0 },
    cms_workbook: { rows: 3 },
    publication_status: "published",
    master_review: {
      filename: "post-master-reviewed.xlsx",
      version: 2,
      published: {
        uploaded_at: "2026-09-11T10:05:00Z",
        database: { groups_created: 1, questions_created: 3, labels_reissued: 0 },
        cms_workbook: { rows: 3 },
      },
    },
    review_workflow: { status: "published" },
  });
});

afterEach(() => {
  confirmSpy.mockRestore();
});

function renderWorkflow(current: UploadJob, onJob = vi.fn()) {
  render(
    <RunConsoleProvider>
      <MasterReviewWorkflow job={current} onJob={onJob} />
    </RunConsoleProvider>,
  );
  return onJob;
}

test("the Master review stage opens at master_ready and stays open once published", () => {
  expect(isMasterReviewStage(job())).toBe(true);
  expect(isMasterReviewStage(job({ review_workflow: { status: "published" } }))).toBe(true);
  expect(isMasterReviewStage(job({ review_workflow: { status: "pending_review" } }))).toBe(false);
  expect(isMasterReviewStage(job({ review_workflow: { status: "master_building" } }))).toBe(false);
  // A legacy generated job carries no marker and never mounts Step 03.
  expect(isMasterReviewStage(job({ review_workflow: undefined }))).toBe(false);
  expect(isMasterReviewStage(job({ review_workflow: null }))).toBe(false);
});

test("renders both lanes with Master and Concept downloads and the unavailable reason", () => {
  renderWorkflow(job());

  expect(screen.getByText("Step 03 · Review Master files & publish")).toBeDefined();
  const postMaster = screen.getByTestId("download-master-post");
  expect(postMaster.tagName).toBe("A");
  expect(postMaster.getAttribute("href")).toBe("/build-assessments/releases/22/master.xlsx");
  expect(screen.getByRole("link", { name: "Download the Post-Learning Concept File" }).getAttribute("href"))
    .toBe("/build-concepts/uploads/55/release-bulk-import.xlsx");
  expect(screen.getByRole("link", { name: "Download the Pre-Learning Concept File" })).toBeDefined();

  const preMaster = screen.getByTestId("download-master-pre") as HTMLButtonElement;
  expect(preMaster.tagName).toBe("BUTTON");
  expect(preMaster.disabled).toBe(true);
  expect(screen.getByTestId("master-unavailable-pre").textContent).toContain("No space left on device");

  expect(screen.getByTestId("upload-reviewed-master-post").textContent)
    .toBe("Upload reviewed Post-Learning Master file");
  expect(screen.getByTestId("upload-reviewed-master-pre").textContent)
    .toBe("Upload reviewed Pre-Learning Master file");
  // The legacy same-act CMS upload is never offered here.
  expect(screen.queryByText(/Upload edited .* Excel to CMS/)).toBeNull();
});

test("uploading a reviewed Master posts to its lane and shows the accepted receipt", async () => {
  const onJob = renderWorkflow(job());

  fireEvent.change(screen.getByTestId("reviewed-master-input-post"), {
    target: { files: [new File(["xlsx"], "post-master-reviewed.xlsx", { type: XLSX })] },
  });

  await waitFor(() => expect(apiMock.uploadReviewedMaster).toHaveBeenCalledWith(
    55, "post", expect.any(File),
  ));
  expect(apiMock.uploadEditedWorkbook).not.toHaveBeenCalled();
  expect(apiMock.publishReviewedMaster).not.toHaveBeenCalled();
  expect(apiMock.uploadConceptRelease).not.toHaveBeenCalled();

  const receipt = await screen.findByTestId("master-receipt-post");
  expect(receipt.textContent).toContain("Accepted file: post-master-reviewed.xlsx");
  expect(receipt.textContent).toContain("Version 2");
  expect(receipt.textContent).toContain("3 fields");
  expect(receipt.textContent).toContain("1 omitted question");
  expect(receipt.textContent).toContain("0 added questions");
  expect(receipt.textContent).toContain("Readiness: ready_with_flags");
  expect(screen.getByTestId("master-issues-post").textContent).toContain("Q-0004 keyword cell is blank");
  expect(screen.getByText("Reviewed Master received")).toBeDefined();
  expect(apiMock.getUploadJob).toHaveBeenCalledWith("concepts", 55);
  expect(onJob).toHaveBeenCalledWith(expect.objectContaining({ id: 55 }));
});

test("the durable marker receipt is shown after a refresh without any local state", () => {
  renderWorkflow(job({
    review_workflow: {
      status: "master_ready",
      master_review: {
        pre: {
          filename: "pre-master-reviewed.xlsx",
          uploaded_at: "2026-09-11T09:00:00Z",
          version: 3,
          changed_fields: 0,
          omitted: 0,
          added: 2,
          readiness: "ready",
          issues: [],
          status: "accepted",
        },
      },
    },
  }));

  const receipt = screen.getByTestId("master-receipt-pre");
  expect(receipt.textContent).toContain("pre-master-reviewed.xlsx");
  expect(receipt.textContent).toContain("Version 3");
  expect(receipt.textContent).toContain("2 added questions");
  expect(screen.queryByTestId("master-issues-pre")).toBeNull();
  expect(screen.queryByTestId("master-receipt-post")).toBeNull();
});

test("Publish Master stays disabled until that lane's Concept file is published", () => {
  renderWorkflow(job());

  const publishMaster = screen.getByTestId("publish-master-post") as HTMLButtonElement;
  expect(publishMaster.disabled).toBe(true);
  expect(screen.getByTestId("publish-master-blocked-post").textContent)
    .toBe("Publish the Post-Learning Concept file first.");
  expect((screen.getByTestId("publish-concept-post") as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByTestId("publish-concept-post").textContent)
    .toBe("Publish Post-Learning Concept file to database & CMS");
  expect(conceptPublicationState(job(), "post")).toEqual({ state: "available" });
});

test("a manifest that records the Concept publication enables Publish Master and labels the Concept as published", () => {
  const current = job({}, { conceptPublished: { post: true } });
  expect(conceptPublicationState(current, "post")).toEqual({ state: "published" });
  renderWorkflow(current);

  const publishConcept = screen.getByTestId("publish-concept-post") as HTMLButtonElement;
  expect(publishConcept.disabled).toBe(true);
  expect(publishConcept.textContent).toBe("Concept file published");
  expect((screen.getByTestId("publish-master-post") as HTMLButtonElement).disabled).toBe(false);
  expect(screen.queryByTestId("publish-master-blocked-post")).toBeNull();
  // The Pre lane is untouched: publication is per lane.
  expect((screen.getByTestId("publish-master-pre") as HTMLButtonElement).disabled).toBe(true);
});

test("publishes the Concept file, then the Master file, each confirmed, in order, with receipts", async () => {
  apiMock.getUploadJob
    .mockResolvedValueOnce(job({}, { conceptPublished: { post: true } }))
    .mockResolvedValueOnce(job({
      review_workflow: {
        status: "published",
        master_review: {
          post: {
            filename: "post-master-reviewed.xlsx",
            version: 2,
            published: {
              uploaded_at: "2026-09-11T10:05:00Z",
              database: { groups_created: 1, questions_created: 3, labels_reissued: 0 },
              cms_workbook: { rows: 3 },
            },
          },
        },
      },
    }, { conceptPublished: { post: true } }));
  const onJob = renderWorkflow(job());

  fireEvent.click(screen.getByTestId("publish-concept-post"));
  await waitFor(() => expect(apiMock.uploadConceptRelease).toHaveBeenCalledWith(55, "post"));
  expect(confirmSpy).toHaveBeenCalledTimes(1);
  expect(String(confirmSpy.mock.calls[0][0])).toContain("Post-Learning Concept file");
  await waitFor(() => expect(onJob).toHaveBeenCalledTimes(1));
  expect(screen.getByTestId("publish-concept-post").textContent).toBe("Concept file published");
  expect((screen.getByTestId("publish-master-post") as HTMLButtonElement).disabled).toBe(false);

  fireEvent.click(screen.getByTestId("publish-master-post"));
  await waitFor(() => expect(apiMock.publishReviewedMaster).toHaveBeenCalledWith(55, "post"));
  expect(confirmSpy).toHaveBeenCalledTimes(2);
  expect(String(confirmSpy.mock.calls[1][0])).toContain("Post-Learning Master file");
  expect(apiMock.uploadConceptRelease.mock.invocationCallOrder[0])
    .toBeLessThan(apiMock.publishReviewedMaster.mock.invocationCallOrder[0]);

  const publication = await screen.findByTestId("master-publication-post");
  expect(publication.textContent).toContain("3 questions");
  expect(publication.textContent).toContain("1 group");
  expect(publication.textContent).toContain("CMS workbook: 3 rows");
  expect(screen.getByTestId("publish-master-post").textContent).toBe("Master file published");
  expect(screen.getByText("Master published")).toBeDefined();
  // The parent receives the refreshed job after each act; the header badge
  // follows that durable status (pinned separately below).
  expect(onJob).toHaveBeenCalledTimes(2);
  expect(onJob).toHaveBeenLastCalledWith(expect.objectContaining({
    review_workflow: expect.objectContaining({ status: "published" }),
  }));
  expect(apiMock.uploadEditedWorkbook).not.toHaveBeenCalled();
});

test("the header badge follows the durable published status and marks the card", () => {
  renderWorkflow(job({
    review_workflow: {
      status: "published",
      master_review: {
        post: {
          filename: "post-master-reviewed.xlsx",
          version: 2,
          published: {
            uploaded_at: "2026-09-11T10:05:00Z",
            database: { groups_created: 1, questions_created: 3 },
          },
        },
      },
    },
  }, { conceptPublished: { post: true } }));

  const badge = screen.getByText("published");
  expect(badge.classList).toContain("green");
  expect(badge.closest("section")?.classList).toContain("is-published");
  expect(screen.getByTestId("master-publication-post").textContent).toContain("3 questions");
  expect(screen.getByTestId("publish-master-post").textContent).toBe("Master file published");
});

test("a declined confirmation publishes nothing", async () => {
  confirmSpy.mockImplementation(() => false);
  renderWorkflow(job({}, { conceptPublished: { post: true } }));

  fireEvent.click(screen.getByTestId("publish-master-post"));
  await Promise.resolve();

  expect(apiMock.publishReviewedMaster).not.toHaveBeenCalled();
  expect(apiMock.uploadConceptRelease).not.toHaveBeenCalled();
  expect(apiMock.getUploadJob).not.toHaveBeenCalled();
});

test("a refused publication is shown readably and the lane stays publishable", async () => {
  apiMock.publishReviewedMaster.mockRejectedValue(
    Object.assign(new Error("Publish the post Concept file first"), { status: 409 }),
  );
  const current = job({}, { conceptPublished: { post: true } });
  apiMock.getUploadJob.mockResolvedValue(current);
  renderWorkflow(current);

  fireEvent.click(screen.getByTestId("publish-master-post"));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Post-Learning Master file could not be published");
  expect(alert.textContent).toContain("Publish the post Concept file first");
  expect(screen.queryByTestId("master-publication-post")).toBeNull();
  expect((screen.getByTestId("publish-master-post") as HTMLButtonElement).disabled).toBe(false);
  expect(apiMock.getUploadJob).toHaveBeenCalledWith("concepts", 55);
});

test("a failed reviewed-Master upload is shown without inventing a receipt", async () => {
  apiMock.uploadReviewedMaster.mockRejectedValue(new Error("422 the workbook is missing the Master sheet"));
  renderWorkflow(job());

  fireEvent.change(screen.getByTestId("reviewed-master-input-pre"), {
    target: { files: [new File(["xlsx"], "bad.xlsx", { type: XLSX })] },
  });

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Pre-Learning reviewed Master file could not be uploaded");
  expect(alert.textContent).toContain("missing the Master sheet");
  expect(screen.queryByTestId("master-receipt-pre")).toBeNull();
});

test("an active run locks every Step 03 control", () => {
  renderWorkflow(job({ generation_running: true }, { conceptPublished: { post: true } }));

  for (const id of [
    "upload-reviewed-master-post",
    "upload-reviewed-master-pre",
    "publish-concept-pre",
    "publish-master-post",
  ]) {
    expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true);
  }
  expect(screen.getByText("run active")).toBeDefined();
});
