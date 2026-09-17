import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import ChapterRowUpload from "./ChapterRowUpload";

const apiMock = vi.hoisted(() => ({
  chapterBatchUploadConceptReview: vi.fn(),
  chapterBatchUploadMasterReview: vi.fn(),
}));
vi.mock("../api/client", () => ({ api: apiMock }));

beforeEach(() => { vi.resetAllMocks(); });

test.each(["concept", "master"] as const)("the chapter drawer stages a %s review with notes before upload", async (kind) => {
  const endpoint = kind === "concept" ? apiMock.chapterBatchUploadConceptReview : apiMock.chapterBatchUploadMasterReview;
  const row = {
    chapter_id: 11,
    review_error_report: { report_id: `${kind}-report`, status: "queued", review_kind: kind, lane: "pre" },
  };
  endpoint.mockResolvedValue(row);
  const onUploaded = vi.fn();
  render(<ChapterRowUpload chapterId={11} slot={kind} lane="pre" scope="drawer" onUploaded={onUploaded} />);
  const id = `chapter-11-drawer-upload-${kind}-pre`;
  const file = new File(["xlsx"], "reviewed.xlsx");
  fireEvent.change(document.getElementById(id)!, { target: { files: [file] } });
  expect(endpoint).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Log errors for maintenance" }));
  fireEvent.change(screen.getByLabelText("What did you correct? (optional)"), {
    target: { value: "Reinstated the question table." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload reviewed file" }));
  await screen.findByText(`${kind}-report`);
  expect(endpoint).toHaveBeenCalledWith(11, "pre", file, "Reinstated the question table.");
  expect(onUploaded).toHaveBeenCalledWith(row);
  await waitFor(() => expect(screen.queryByLabelText("What did you correct? (optional)")).toBeNull());
});

test("row and drawer review forms keep separate drafts and omit an unrequested report", async () => {
  apiMock.chapterBatchUploadConceptReview.mockResolvedValue({ chapter_id: 11 });
  render(<>
    <ChapterRowUpload chapterId={11} slot="concept" lane="post" onUploaded={vi.fn()} />
    <ChapterRowUpload chapterId={11} slot="concept" lane="post" scope="drawer" onUploaded={vi.fn()} />
  </>);
  const rowId = "chapter-11-upload-concept-post";
  const drawerId = "chapter-11-drawer-upload-concept-post";
  fireEvent.change(document.getElementById(rowId)!, { target: { files: [new File(["a"], "row.xlsx")] } });
  fireEvent.change(document.getElementById(drawerId)!, { target: { files: [new File(["b"], "drawer.xlsx")] } });
  const rowForm = within(screen.getByTestId(`${rowId}-form`));
  const drawerForm = within(screen.getByTestId(`${drawerId}-form`));
  fireEvent.click(rowForm.getByRole("checkbox", { name: "Log errors for maintenance" }));
  fireEvent.change(rowForm.getByLabelText("What did you correct? (optional)"), { target: { value: "Keep this draft." } });
  fireEvent.click(drawerForm.getByRole("button", { name: "Upload reviewed file" }));
  await waitFor(() => expect(apiMock.chapterBatchUploadConceptReview).toHaveBeenCalledWith(
    11, "post", expect.objectContaining({ name: "drawer.xlsx" }), undefined,
  ));
  expect((rowForm.getByLabelText("What did you correct? (optional)") as HTMLTextAreaElement).value).toBe("Keep this draft.");
});
