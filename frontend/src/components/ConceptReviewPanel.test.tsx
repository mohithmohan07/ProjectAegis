import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { ConceptRevision } from "../types";

const apiMock = vi.hoisted(() => ({
  conceptReleaseUrl: vi.fn((id: number) => `/release/${id}.xlsx`),
  conceptReleaseBulkImportUrl: vi.fn(
    (id: number) => `/release/${id}-bulk-import.xlsx`,
  ),
  inventoryCsvUrl: vi.fn((id: number) => `/inventory/${id}.csv`),
  listConceptRevisions: vi.fn(),
  submitConceptRevision: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMock }));

import { ConceptReviewPanel } from "./ConceptReviewPanel";

function revision(overrides: Partial<ConceptRevision> = {}): ConceptRevision {
  return {
    id: 1,
    job_id: 7,
    round_number: 1,
    instruction: "Move it under Sum of First n Terms.",
    status: "applied",
    change_summary: "Moved to the later topic.",
    changes: [],
    change_count: 1,
    flagged_placements: [],
    model: "gpt-5.6-luna",
    error: "",
    created_at: "2026-08-05T10:00:00",
    completed_at: "2026-08-05T10:00:05",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listConceptRevisions.mockResolvedValue({ job_id: 7, revisions: [] });
});

test("offers the current output for download", async () => {
  render(<ConceptReviewPanel jobId={7} />);

  const bulkImport = await screen.findByTestId("download-bulk-import");
  expect(bulkImport.getAttribute("href")).toBe(
    "/release/7-bulk-import.xlsx",
  );
  const inventory = await screen.findByTestId("download-inventory");
  expect(inventory.getAttribute("href")).toBe("/inventory/7.csv");
  const review = await screen.findByTestId("download-output");
  expect(review.getAttribute("href")).toBe("/release/7.xlsx");
});

test("submits an instruction and refreshes the history", async () => {
  apiMock.submitConceptRevision.mockResolvedValue(revision());
  apiMock.listConceptRevisions
    .mockResolvedValueOnce({ job_id: 7, revisions: [] })
    .mockResolvedValueOnce({ job_id: 7, revisions: [revision()] });

  render(<ConceptReviewPanel jobId={7} />);

  const box = await screen.findByLabelText("What needs changing?");
  fireEvent.change(box, {
    target: { value: "Move it under Sum of First n Terms." },
  });
  fireEvent.click(screen.getByText("Apply changes"));

  await waitFor(() =>
    expect(apiMock.submitConceptRevision).toHaveBeenCalledWith(
      7,
      "Move it under Sum of First n Terms.",
    ),
  );
  expect(
    (await screen.findByTestId("revision-history")).textContent,
  ).toContain("Round 1");
});

test("there is no round limit: history keeps growing", async () => {
  const many = Array.from({ length: 30 }, (_, index) =>
    revision({ id: index + 1, round_number: index + 1 }),
  );
  apiMock.listConceptRevisions.mockResolvedValue({
    job_id: 7,
    revisions: many,
  });

  render(<ConceptReviewPanel jobId={7} />);

  const history = await screen.findByTestId("revision-history");
  expect(history.textContent).toContain("Round 30");
  // Nothing disables the box once rounds accumulate.
  const box = screen.getByLabelText("What needs changing?") as HTMLTextAreaElement;
  expect(box.disabled).toBe(false);
});

test("shows what Aegis placed by its own reading", async () => {
  apiMock.listConceptRevisions.mockResolvedValue({
    job_id: 7,
    revisions: [
      revision({
        flagged_placements: [
          { level: "warning", message: "Phase 3.8 narrowed 'How d governs Sn'" },
        ],
      }),
    ],
  });

  render(<ConceptReviewPanel jobId={7} />);

  expect(
    (await screen.findByTestId("flagged-placements")).textContent,
  ).toContain("How d governs Sn");
});

test("a failed round surfaces its error without losing the history", async () => {
  apiMock.listConceptRevisions.mockResolvedValue({
    job_id: 7,
    revisions: [
      revision({ status: "failed", error: "provider unavailable" }),
    ],
  });

  render(<ConceptReviewPanel jobId={7} />);

  const history = await screen.findByTestId("revision-history");
  expect(history.textContent).toContain("failed");
  expect(history.textContent).toContain("provider unavailable");
  expect(history.textContent).toContain("Move it under Sum of First n Terms.");
});

test("an empty instruction cannot be submitted", async () => {
  render(<ConceptReviewPanel jobId={7} />);

  const button = (await screen.findByText("Apply changes")) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("What needs changing?"), {
    target: { value: "   " },
  });
  expect(button.disabled).toBe(true);
  expect(apiMock.submitConceptRevision).not.toHaveBeenCalled();
});
