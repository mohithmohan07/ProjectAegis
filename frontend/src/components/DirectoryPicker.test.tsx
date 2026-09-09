import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

test("uses clean display names and hides hierarchy identity tags", async () => {
  apiMock.tree.mockResolvedValue([{
    board: "NCF",
    grades: [{
      grade: "01",
      subjects: [{
        subject: "English",
        units: [{
          unit: "Prose (01_NCF)",
          chapters: [{
            id: 101,
            chapter_code: "01XXEN_ThankYouTaff",
            chapter_title: "Seasons(01_EVS_NCF)",
            chapter_display_name: "",
            topic_count: 1,
            concept_count: 1,
          }],
        }],
      }],
    }],
  }]);
  apiMock.chapter.mockResolvedValue({
    id: 101,
    chapter_code: "01XXEN_ThankYouTaff",
    chapter_title: "Seasons(01_EVS_NCF)",
    chapter_display_name: "",
    board: "NCF",
    grade: "01",
    subject: "English",
    unit: "Prose (01_NCF)",
    topics: [{
      id: 201,
      topic_title: "Reading (01XXEN_ThankYouTaff_PL)",
      topic_display_name: "Reading",
      pre_post_learning: "Post",
      concepts: [{
        id: 301,
        concept_title: "Meaning (01XXEN_ThankYouTaff_PL_Meaning)",
        concept_display_name: "Meaning",
        group_count: 0,
        question_count: 0,
      }],
    }],
  });

  const onScope = vi.fn();
  render(<DirectoryPicker onScope={onScope} />);
  // The controls exist before the asynchronous directory request resolves.
  // Wait for its option before selecting; changing an empty select loses the
  // value on slower CI runners and prevents the remaining hierarchy loading.
  await screen.findByRole("option", { name: "NCF" });
  const selects = await screen.findAllByRole("combobox");
  fireEvent.change(selects[0], { target: { value: "NCF" } });
  fireEvent.change(selects[1], { target: { value: "01" } });
  fireEvent.change(selects[2], { target: { value: "English" } });
  fireEvent.change(selects[3], { target: { value: "Prose (01_NCF)" } });

  expect(await screen.findByRole("option", {
    name: "Seasons (1 concepts)",
  })).toBeDefined();
  expect(screen.queryByText(/01_EVS_NCF|01_NCF/)).toBeNull();

  fireEvent.change(selects[4], { target: { value: "101" } });
  fireEvent.click(await screen.findByRole("radio", { name: "Specific concepts" }));
  expect(await screen.findByText("Meaning")).toBeDefined();
  expect(screen.getByText("Reading")).toBeDefined();
  expect(screen.queryByText(/01XXEN_ThankYouTaff/)).toBeNull();
  fireEvent.click(screen.getByText("Meaning"));
  await waitFor(() => {
    expect(onScope).toHaveBeenLastCalledWith({
      type: "concept",
      ids: [301],
      label: "1 concept(s) in Seasons",
    });
  });
});
