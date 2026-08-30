import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import type { ConceptRevision } from "../types";

const apiMock = vi.hoisted(() => ({
  inventoryCsvUrl: vi.fn((id: number) => `/inventory/${id}.csv`),
  listConceptRevisions: vi.fn(),
  submitConceptRevision: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMock }));

import { ConceptReviewPanel } from "./ConceptReviewPanel";

// The panel carries a <Link> to the release-review page, so a Router is
// required here exactly as in the app.
function renderPanel() {
  return render(
    <MemoryRouter>
      <ConceptReviewPanel jobId={7} />
    </MemoryRouter>,
  );
}

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

test("hosts no downloads of its own — the Run outputs section owns them", async () => {
  renderPanel();

  // The panel used to duplicate Output 03 and the diagnostics as its own
  // download cards; the manifest's Run outputs grid is now the one home.
  await screen.findByTestId("open-review-page");
  expect(screen.queryByTestId("download-bulk-import")).toBeNull();
  expect(screen.queryByTestId("download-inventory")).toBeNull();
  expect(screen.queryByTestId("download-output")).toBeNull();
  expect(screen.getByText(/Run outputs section above/i)).toBeDefined();
});

test("deep-links to the release review page for this job", async () => {
  renderPanel();

  const link = await screen.findByTestId("open-review-page");
  expect(link.getAttribute("href")).toBe("/build-concepts/review/7?lane=post");
});

test("submits an instruction and refreshes the history", async () => {
  apiMock.submitConceptRevision.mockResolvedValue(revision());
  apiMock.listConceptRevisions
    .mockResolvedValueOnce({ job_id: 7, revisions: [] })
    .mockResolvedValueOnce({ job_id: 7, revisions: [revision()] });

  renderPanel();

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

  renderPanel();

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

  renderPanel();

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

  renderPanel();

  const history = await screen.findByTestId("revision-history");
  expect(history.textContent).toContain("failed");
  expect(history.textContent).toContain("provider unavailable");
  expect(history.textContent).toContain("Move it under Sum of First n Terms.");
});

test("an empty instruction cannot be submitted", async () => {
  renderPanel();

  const button = (await screen.findByText("Apply changes")) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("What needs changing?"), {
    target: { value: "   " },
  });
  expect(button.disabled).toBe(true);
  expect(apiMock.submitConceptRevision).not.toHaveBeenCalled();
});

