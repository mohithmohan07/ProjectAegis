import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import DirectoryPicker from "./DirectoryPicker";

const apiMock = vi.hoisted(() => ({
  tree: vi.fn(),
  chapter: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: apiMock,
}));

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.tree.mockResolvedValue([{
    board: "CBSE",
    grades: [{
      grade: "10",
      subjects: [{
        subject: "Science",
        units: [{
          unit: "Electricity and Magnetism",
          chapters: [{
            id: 77,
            chapter_code: "CH-11",
            chapter_title: "Electricity",
            chapter_display_name: "Electricity",
            topic_count: 0,
            concept_count: 0,
          }],
        }],
      }],
    }],
  }]);
  apiMock.chapter.mockResolvedValue({
    id: 77,
    chapter_code: "CH-11",
    chapter_title: "Electricity",
    chapter_display_name: "Electricity",
    board: "CBSE",
    grade: "10",
    subject: "Science",
    unit: "Electricity and Magnetism",
    topics: [],
  });
});

test("selects a checkpoint chapter by stable identity without starting work", async () => {
  const onScope = vi.fn();
  render(
    <DirectoryPicker
      onScope={onScope}
      chapterOnly
      initialChapterIdentity={{
        board: "cbse",
        grade: "10",
        subject: "science",
        unit: "electricity and magnetism",
        chapter_title: "electricity",
        chapter_code: "ch-11",
      }}
    />,
  );

  expect(
    await screen.findByText("Saved checkpoint target selected: Electricity."),
  ).toBeDefined();
  await waitFor(() => {
    expect(onScope).toHaveBeenLastCalledWith({
      type: "chapter",
      ids: [77],
      label: "Electricity",
    });
  });
});

test("keeps generation unselected when the saved target is absent", async () => {
  const onScope = vi.fn();
  render(
    <DirectoryPicker
      onScope={onScope}
      chapterOnly
      initialChapterIdentity={{
        board: "cbse",
        grade: "10",
        subject: "science",
        unit: "missing unit",
        chapter_title: "missing chapter",
        chapter_code: "missing",
      }}
    />,
  );

  expect(await screen.findByText(/saved destination is not/i)).toBeDefined();
  expect(onScope).toHaveBeenLastCalledWith(null);
});
