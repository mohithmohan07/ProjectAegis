import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import type { ReleaseReviewView } from "../types";

const apiMock = vi.hoisted(() => ({
  getReleaseReview: vi.fn(),
  submitReleaseManualEdit: vi.fn(),
  applyReleaseInstruction: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMock }));

import ReleaseReview from "./ReleaseReview";

function makeView(overrides: Partial<ReleaseReviewView> = {}): ReleaseReviewView {
  return {
    job_id: 7,
    lane: "post",
    staged_version: 3,
    staged_release_uid: "uid-3",
    state: "ready_with_flags",
    summary: {
      row_count: 3,
      issue_count: 1,
      error_count: 0,
      warning_count: 1,
      database_uploaded: false,
    },
    topics: [
      {
        topic: "Arithmetic Progressions",
        concepts: [
          {
            record_index: 0,
            parent_concept: "Sequences",
            concept_title: "Common difference",
            concept_details:
              "The gap d between consecutive terms. // " +
              "Formula: [Katex]d = a_{n+1} - a_n[/Katex] // " +
              'Figure: [img src="https://cdn.example/ap.png" alt="AP number line"]',
            keywords: "common difference, d",
            review_flags: ["placed by best reading"],
            release_status: "released",
            release_errors: [],
          },
          {
            record_index: 1,
            parent_concept: "Sequences",
            concept_title: "General term",
            concept_details: "The nth term of an AP.",
            keywords: "nth term",
            review_flags: [],
            release_status: "released",
            release_errors: [],
          },
        ],
      },
      {
        topic: "Sum of First n Terms",
        concepts: [
          {
            record_index: 2,
            parent_concept: "Series",
            concept_title: "Sum formula",
            concept_details:
              "Adding the first n terms. // " +
              "Link: [derivation](https://example.com/derivation)",
            keywords: "sum, Sn",
            review_flags: [],
            release_status: "released",
            release_errors: [],
          },
        ],
      },
    ],
    issues: [
      {
        code: "W101",
        severity: "warning",
        message: "One placement was flagged for review.",
      },
    ],
    versions: [
      {
        version: 1,
        staged_release_uid: "uid-1",
        origin: "staged",
        instruction: null,
        change_count: 0,
        created_at: "2026-08-19T10:00:00",
      },
      {
        version: 3,
        staged_release_uid: "uid-3",
        origin: "instruction",
        instruction: "Reword the sum concept.",
        change_count: 2,
        created_at: "2026-08-20T09:00:00",
      },
    ],
    ...overrides,
  };
}

function renderPage(path = "/build-concepts/review/7?lane=post") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/build-concepts/review/:jobId" element={<ReleaseReview />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getReleaseReview.mockResolvedValue(makeView());
});

test("renders topics, concept cards, flags and the rendered details", async () => {
  renderPage();

  const topics = await screen.findAllByTestId("review-topic");
  expect(topics.length).toBe(2);
  expect(topics[0].textContent).toContain("Arithmetic Progressions");
  expect(topics[1].textContent).toContain("Sum of First n Terms");

  const cards = screen.getAllByTestId("review-concept");
  expect(cards.length).toBe(3);
  expect(cards[0].textContent).toContain("Common difference");
  expect(cards[0].textContent).toContain("Sequences");
  expect(cards[0].textContent).toContain("common difference, d");
  // The one review flag renders as a badge.
  expect(cards[0].textContent).toContain("placed by best reading");

  // Details are rendered, not shown raw: katex -> code node, https img -> <img>.
  const code = cards[0].querySelector("code.katex-inline");
  expect(code).not.toBeNull();
  expect(code!.textContent).toBe("d = a_{n+1} - a_n");
  const img = screen.getByAltText("AP number line");
  expect(img.getAttribute("src")).toBe("https://cdn.example/ap.png");
  const anchor = cards[2].querySelector("a");
  expect(anchor!.getAttribute("href")).toBe("https://example.com/derivation");

  // State badge, summary, issues, versions.
  expect(screen.getByText("ready_with_flags")).toBeTruthy();
  expect(screen.getByTestId("review-summary").textContent).toContain("3 rows");
  expect(screen.getByTestId("issues-list").textContent).toContain(
    "One placement was flagged for review.",
  );
  const history = screen.getByTestId("version-history");
  expect(history.textContent).toContain("v1");
  expect(history.textContent).toContain("Reword the sum concept.");

  // The lane came from the search param.
  expect(apiMock.getReleaseReview).toHaveBeenCalledWith(7, "post");

  // Publication stays on the Build Concepts page (Rule G) — link only.
  const publish = screen.getByText("Open Output workbooks & publish");
  expect(publish.getAttribute("href")).toBe("/build-concepts");
});

test("reads lane=pre from the URL and defaults to post without one", async () => {
  renderPage("/build-concepts/review/7?lane=pre");
  await waitFor(() =>
    expect(apiMock.getReleaseReview).toHaveBeenCalledWith(7, "pre"),
  );
});

test("edit flow posts only the changed fields, with before values, in ONE call", async () => {
  const updated = makeView({
    staged_version: 4,
    staged_release_uid: "uid-4",
  });
  updated.topics[0].concepts[0].concept_title = "Common difference d";
  updated.topics[0].concepts[0].keywords = "common difference, d, gap";
  apiMock.submitReleaseManualEdit.mockResolvedValue(updated);

  renderPage();

  fireEvent.click((await screen.findAllByTestId("edit-concept"))[0]);

  // The form is prefilled from the card (topic comes from the section).
  const topicInput = screen.getByTestId("edit-field-topic") as HTMLInputElement;
  expect(topicInput.value).toBe("Arithmetic Progressions");
  const titleInput = screen.getByTestId(
    "edit-field-concept_title",
  ) as HTMLInputElement;
  expect(titleInput.value).toBe("Common difference");

  fireEvent.change(titleInput, { target: { value: "Common difference d" } });
  fireEvent.change(screen.getByTestId("edit-field-keywords"), {
    target: { value: "common difference, d, gap" },
  });
  fireEvent.click(screen.getByTestId("save-edit"));

  await waitFor(() =>
    expect(apiMock.submitReleaseManualEdit).toHaveBeenCalledTimes(1),
  );
  expect(apiMock.submitReleaseManualEdit).toHaveBeenCalledWith(7, {
    lane: "post",
    staged_release_uid: "uid-3",
    edits: [
      {
        record_index: 0,
        field: "concept_title",
        before: "Common difference",
        after: "Common difference d",
      },
      {
        record_index: 0,
        field: "keywords",
        before: "common difference, d",
        after: "common difference, d, gap",
      },
    ],
  });

  // The whole view is replaced by the response and the form closes.
  await screen.findByText("Common difference d");
  expect(screen.queryByTestId("save-edit")).toBeNull();
  expect(screen.getByTestId("review-summary").textContent).toContain("v4");
});

test("a stale edit (409) surfaces the reload message and keeps the form open", async () => {
  apiMock.submitReleaseManualEdit.mockRejectedValue(new Error("409 Conflict"));

  renderPage();

  fireEvent.click((await screen.findAllByTestId("edit-concept"))[0]);
  fireEvent.change(screen.getByTestId("edit-field-concept_title"), {
    target: { value: "Renamed while someone else was editing" },
  });
  fireEvent.click(screen.getByTestId("save-edit"));

  const alert = await screen.findByText(/changed under you/i);
  expect(alert.textContent).toMatch(/reload/i);
  // The reviewer's draft is not thrown away.
  expect(screen.getByTestId("save-edit")).toBeTruthy();
});

test("cancel restores the card without posting anything", async () => {
  renderPage();

  fireEvent.click((await screen.findAllByTestId("edit-concept"))[0]);
  fireEvent.change(screen.getByTestId("edit-field-concept_title"), {
    target: { value: "A change I think better of" },
  });
  fireEvent.click(screen.getByText("Cancel"));

  expect(screen.queryByTestId("save-edit")).toBeNull();
  expect(screen.getByText("Common difference")).toBeTruthy();
  expect(apiMock.submitReleaseManualEdit).not.toHaveBeenCalled();
});

test("instruction flow posts against the staged uid and clears the box on success", async () => {
  apiMock.applyReleaseInstruction.mockResolvedValue(
    makeView({ staged_version: 4, staged_release_uid: "uid-4" }),
  );

  renderPage();

  const box = (await screen.findByTestId("instruction-box")) as HTMLTextAreaElement;
  fireEvent.change(box, {
    target: { value: "Move General term under Sum of First n Terms." },
  });
  fireEvent.click(screen.getByTestId("apply-instruction"));

  await waitFor(() =>
    expect(apiMock.applyReleaseInstruction).toHaveBeenCalledWith(7, {
      lane: "post",
      staged_release_uid: "uid-3",
      instruction: "Move General term under Sum of First n Terms.",
    }),
  );
  await waitFor(() => expect(box.value).toBe(""));
  expect(screen.getByTestId("review-summary").textContent).toContain("v4");
});

test("a failed instruction round shows the detail and keeps the text", async () => {
  apiMock.applyReleaseInstruction.mockRejectedValue(
    new Error("model round failed: provider returned malformed JSON"),
  );

  renderPage();

  const box = (await screen.findByTestId("instruction-box")) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "Shorten every keyword list." } });
  fireEvent.click(screen.getByTestId("apply-instruction"));

  await screen.findByText("model round failed: provider returned malformed JSON");
  // Not cleared — the reviewer retries without retyping.
  expect(box.value).toBe("Shorten every keyword list.");
});

test("the apply button is disabled while a round is in flight and when empty", async () => {
  let resolveRound: (view: ReleaseReviewView) => void = () => undefined;
  apiMock.applyReleaseInstruction.mockImplementation(
    () => new Promise<ReleaseReviewView>((resolve) => (resolveRound = resolve)),
  );

  renderPage();

  const button = (await screen.findByTestId(
    "apply-instruction",
  )) as HTMLButtonElement;
  expect(button.disabled).toBe(true); // empty box

  fireEvent.change(screen.getByTestId("instruction-box"), {
    target: { value: "Do the thing." },
  });
  expect(button.disabled).toBe(false);

  fireEvent.click(button);
  await waitFor(() => expect(button.disabled).toBe(true)); // in flight

  resolveRound(makeView({ staged_version: 4 }));
  await waitFor(() => expect(button.textContent).toBe("Apply changes"));
});
