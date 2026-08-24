import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { RunConsoleProvider } from "../RunConsole";
import type { UploadJob } from "../types";
import DocumentUpload from "./DocumentUpload";

const streamNdjsonMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  getUploadJob: vi.fn(),
  rebuildMasterFromConceptJob: vi.fn(),
  uploadConceptRelease: vi.fn(),
  uploadEditedWorkbook: vi.fn(),
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
  window.localStorage.clear();
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


// --------------------------------------------------------------------------- #
// Rule G, per lane: one job stages two releases, each with its own publish act.
//
// The manifest offers a publish entry per lane and both carry action:"post".
// The handler used to ignore the entry it was rendered from and call the
// lane-less endpoint, whose server-side default is "post" — so the
// Pre-Learning button published Outputs 01/02: a wrong-lane, authenticated,
// explicitly-confirmed write, and a silent no-op reporting success whenever
// Post was already published.
// --------------------------------------------------------------------------- #

function bothLanesJob(): UploadJob {
  const job = convertedJob();
  job.status = "released";
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.files = [
    {
      kind: "database_upload",
      label: "Upload released output to database",
      filename: "",
      media_type: "application/json",
      size_bytes: 0,
      download_url: "/build-concepts/uploads/81/upload-release",
      action: "post",
      requires_confirmation: true,
    },
    {
      kind: "pre_database_upload",
      label: "Upload released Pre-Learning output to database",
      filename: "",
      media_type: "application/json",
      size_bytes: 0,
      download_url: "/build-concepts/uploads/81/upload-release?lane=pre",
      action: "post",
      requires_confirmation: true,
    },
  ];
  return job;
}

test.each([
  ["pre", "Pre-Learning"],
  ["post", "Post-Learning"],
] as const)(
  "the edited-workbook control uploads its OWN lane (%s)",
  async (lane, laneLabel) => {
    // Owner steer 2026-08-20: the CMS is fed by the reviewer's edited
    // Excel, applied as a recorded round + published in one act. The
    // separate "upload to database" button is gone.
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockImplementation(() => true);
    apiMock.uploadEditedWorkbook.mockResolvedValue({ changed_fields: 2 });
    apiMock.getUploadJob.mockResolvedValue(bothLanesJob());

    render(
      <RunConsoleProvider>
        <DocumentUpload
          module="concepts"
          conceptKind="post"
          externalJob={bothLanesJob()}
          onJob={vi.fn()}
        />
      </RunConsoleProvider>,
    );

    const file = new File(["x"], "edited.xlsx", {
      type: "application/vnd.openxmlformats-officedocument"
        + ".spreadsheetml.sheet",
    });
    const input = screen.getByTestId(`upload-edited-${lane}`);
    await import("@testing-library/react").then(({ fireEvent }) =>
      fireEvent.change(input, { target: { files: [file] } }));

    await vi.waitFor(() =>
      expect(apiMock.uploadEditedWorkbook).toHaveBeenCalledWith(
        81, lane, file,
      ),
    );
    // ...and the reviewer was told WHICH lane they were authorising.
    expect(confirmSpy.mock.calls[0][0]).toContain(laneLabel);
    confirmSpy.mockRestore();
  },
);

// --------------------------------------------------------------------------- #
// Owner report 2026-08-21: both Pre files downloaded EMPTY with the recorded
// reason nowhere on the page. An enabled output that will be empty now carries
// the run's own recorded reason (note) beside its live Download link.
// --------------------------------------------------------------------------- #

test("an enabled-but-empty Pre output shows the recorded reason and still downloads", () => {
  const job = convertedJob();
  job.status = "released";
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.files = [
    {
      kind: "pre_release_bulk_import",
      label: "Download the Pre-Learning Concept File",
      filename: "ch_pre_bulk_import.xlsx",
      media_type: "application/vnd.openxmlformats-officedocument"
        + ".spreadsheetml.sheet",
      size_bytes: 0,
      download_url: "/build-concepts/uploads/81/release-bulk-import.xlsx?lane=pre",
      action: "download",
      note: "This run REFUSED the Pre-Learning map, so the Pre files carry "
        + "no concepts. Recorded reason: a Pre row carried the identity of "
        + "source question QID-3.",
    },
  ];

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

  // The reason leads with its first sentence and folds the rest (the
  // sentence appears in the fold's summary and again in its body).
  expect(
    screen.getAllByText(/This run REFUSED the Pre-Learning map/).length,
  ).toBeGreaterThan(0);
  // Rule E: the download link is still live next to the reason.
  expect(
    screen.getByRole("link", {
      name: "Download the Pre-Learning Concept File",
    }),
  ).toBeDefined();
});

// --------------------------------------------------------------------------- #
// A Master-lane fault must be recoverable from the already-frozen Concept
// release. Rebuilding one Master is an explicit lane-specific act: it never
// reruns Concept generation and can never fall through to the sibling lane.
// --------------------------------------------------------------------------- #

function failedMastersJob(): UploadJob {
  const job = convertedJob();
  job.status = "released";
  if (!job.source_artifacts) throw new Error("fixture missing artifacts");
  job.source_artifacts.files = [
    {
      kind: "pre_release_bulk_import",
      label: "Download the Pre-Learning Concept File",
      filename: "pre_concepts.xlsx",
      media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      size_bytes: 1024,
      download_url: "/build-concepts/uploads/81/release-bulk-import.xlsx?lane=pre",
      action: "download",
    },
    {
      kind: "pre_release_master",
      label: "Pre-Learning Master File",
      filename: "",
      media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      size_bytes: 0,
      download_url: "",
      action: "download",
      disabled: true,
      disabled_reason: "The 'pre' lane's Master File was not built: OSError: [Errno 28] No space left on device.",
    },
    {
      kind: "release_bulk_import",
      label: "Download the Post-Learning Concept File",
      filename: "post_concepts.xlsx",
      media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      size_bytes: 1024,
      download_url: "/build-concepts/uploads/81/release-bulk-import.xlsx",
      action: "download",
    },
    {
      kind: "release_master",
      label: "Post-Learning Master File",
      filename: "",
      media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      size_bytes: 0,
      download_url: "",
      action: "download",
      disabled: true,
      disabled_reason: "The 'post' lane's Master File was not built: OSError: [Errno 28] No space left on device.",
    },
  ];
  return job;
}

function rebuiltMasterJob(lane: "pre" | "post"): UploadJob {
  const job = failedMastersJob();
  const kind = lane === "pre" ? "pre_release_master" : "release_master";
  const master = job.source_artifacts?.files.find(
    (artifact) => artifact.kind === kind,
  );
  if (!master) throw new Error("fixture missing Master");
  master.disabled = false;
  master.disabled_reason = undefined;
  master.label = `Download the ${lane === "pre" ? "Pre-Learning" : "Post-Learning"} Master File`;
  master.filename = `${lane}_master.xlsx`;
  master.download_url = `/build-assessments/releases/${lane === "pre" ? 21 : 22}/master.xlsx`;
  return job;
}

test.each([
  ["pre", "Pre-Learning"],
  ["post", "Post-Learning"],
] as const)(
  "rebuilds only the disabled %s Master and refreshes its Download",
  async (lane, laneLabel) => {
    apiMock.rebuildMasterFromConceptJob.mockResolvedValue({ id: 22 });
    apiMock.getUploadJob.mockResolvedValue(rebuiltMasterJob(lane));
    const onJob = vi.fn();

    render(
      <RunConsoleProvider>
        <DocumentUpload
          module="concepts"
          conceptKind="post"
          externalJob={failedMastersJob()}
          onJob={onJob}
        />
      </RunConsoleProvider>,
    );

    fireEvent.click(screen.getByRole("button", {
      name: `Rebuild ${laneLabel} Master File`,
    }));

    await vi.waitFor(() =>
      expect(apiMock.rebuildMasterFromConceptJob).toHaveBeenCalledWith(81, lane),
    );
    await vi.waitFor(() => expect(apiMock.getUploadJob).toHaveBeenCalledWith(
      "concepts", 81,
    ));
    await vi.waitFor(() => expect(onJob).toHaveBeenCalled());
    expect(screen.getByRole("link", {
      name: `Download the ${laneLabel} Master File`,
    })).toBeDefined();
    expect(screen.getByText(`${laneLabel} Master File rebuilt. Download is ready.`))
      .toBeDefined();
  },
);

test("a Master rebuild suppresses a duplicate click in the same lane", async () => {
  let finish: ((value: unknown) => void) | undefined;
  apiMock.rebuildMasterFromConceptJob.mockImplementation(() =>
    new Promise((resolve) => { finish = resolve; }));

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={failedMastersJob()}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  const button = screen.getByRole("button", {
    name: "Rebuild Pre-Learning Master File",
  });
  fireEvent.click(button);
  fireEvent.click(button);

  expect(apiMock.rebuildMasterFromConceptJob).toHaveBeenCalledTimes(1);
  expect(button.hasAttribute("disabled")).toBe(true);
  expect(button.textContent).toContain("Rebuilding");
  expect(screen.getByRole("button", {
    name: "Rebuild Post-Learning Master File",
  }).hasAttribute("disabled")).toBe(true);

  finish?.({ id: 21 });
});

test("a 507 gives actionable storage recovery and preserves Concept downloads", async () => {
  const failed = failedMastersJob();
  apiMock.rebuildMasterFromConceptJob.mockRejectedValue(
    Object.assign(new Error("Insufficient Storage"), { status: 507 }),
  );
  apiMock.getUploadJob.mockResolvedValue(failed);
  const onJob = vi.fn();

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={failed}
        onJob={onJob}
      />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByRole("button", {
    name: "Rebuild Post-Learning Master File",
  }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("Server storage is full");
  expect(alert.textContent).toContain("restore capacity on the server filesystem");
  expect(alert.textContent).toContain("do not rerun concept generation");
  expect(apiMock.getUploadJob).toHaveBeenCalledWith("concepts", 81);
  expect(onJob).toHaveBeenCalledWith(failed);
  expect(screen.getByRole("link", {
    name: "Download the Post-Learning Concept File",
  })).toBeDefined();
});

test("a lost response is reconciled from the refreshed durable Master", async () => {
  apiMock.rebuildMasterFromConceptJob.mockRejectedValue(
    new Error("network connection lost"),
  );
  apiMock.getUploadJob.mockResolvedValue(rebuiltMasterJob("post"));

  render(
    <RunConsoleProvider>
      <DocumentUpload
        module="concepts"
        conceptKind="post"
        externalJob={failedMastersJob()}
        onJob={vi.fn()}
      />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByRole("button", {
    name: "Rebuild Post-Learning Master File",
  }));

  expect(await screen.findByText(
    "Post-Learning Master File rebuilt. Download is ready.",
  )).toBeDefined();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("link", {
    name: "Download the Post-Learning Master File",
  })).toBeDefined();
});

test("disables both rebuild controls while this job is already running", () => {
  const job = failedMastersJob();
  job.generation_running = true;

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

  for (const laneLabel of ["Pre-Learning", "Post-Learning"]) {
    expect(screen.getByRole("button", {
      name: `Rebuild ${laneLabel} Master File`,
    }).hasAttribute("disabled")).toBe(true);
  }
});

test("does not offer a Pre Master rebuild when no Pre Concept was staged", () => {
  const job = failedMastersJob();
  const preConcept = job.source_artifacts?.files.find(
    (artifact) => artifact.kind === "pre_release_bulk_import",
  );
  if (!preConcept) throw new Error("fixture missing Pre Concept");
  preConcept.disabled = true;
  preConcept.download_url = "";
  preConcept.disabled_reason = "This run staged no Pre-Learning release.";

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

  expect(screen.queryByRole("button", {
    name: "Rebuild Pre-Learning Master File",
  })).toBeNull();
  expect(screen.getByRole("button", {
    name: "Rebuild Post-Learning Master File",
  })).toBeDefined();
});
