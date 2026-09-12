import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import DirectoryPicker from "./DirectoryPicker";

const apiMock = vi.hoisted(() => ({
  tree: vi.fn(),
  chapter: vi.fn(),
  resolveSavedChapter: vi.fn(),
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
  apiMock.resolveSavedChapter.mockResolvedValue({
    resolved: true,
    reason: "",
    chapter: {
      id: 77,
      chapter_code: "CH-11",
      chapter_title: "Electricity",
      chapter_display_name: "Electricity",
    },
    board: "CBSE",
    grade: "10",
    subject: "Science",
    unit: "Electricity and Magnetism",
    subject_folded: false,
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

test("selects the folded subject's chapter for a checkpoint saved under History", async () => {
  // The checkpoint stored the chapter's RAW subject; the directory shows the
  // same chapter under Social Science. The resolver crosses that fold.
  apiMock.tree.mockResolvedValue([{
    board: "CBSE",
    grades: [{
      grade: "10",
      subjects: [{
        subject: "Social Science",
        units: [{
          unit: "India and the Contemporary World II",
          chapters: [{
            id: 129,
            chapter_code: "10CBSS_PrintCulture",
            chapter_title: "Print Culture and the Modern World",
            chapter_display_name: "",
            topic_count: 4,
            concept_count: 12,
          }],
        }],
      }],
    }],
  }]);
  apiMock.chapter.mockResolvedValue({
    id: 129,
    chapter_code: "10CBSS_PrintCulture",
    chapter_title: "Print Culture and the Modern World",
    chapter_display_name: "",
    board: "CBSE",
    grade: "10",
    subject: "History",
    unit: "India and the Contemporary World II",
    topics: [],
  });
  apiMock.resolveSavedChapter.mockResolvedValue({
    resolved: true,
    reason: "",
    chapter: {
      id: 129,
      chapter_code: "10CBSS_PrintCulture",
      chapter_title: "Print Culture and the Modern World",
      chapter_display_name: "",
    },
    board: "CBSE",
    grade: "10",
    subject: "Social Science",
    unit: "India and the Contemporary World II",
    subject_folded: true,
  });

  const savedIdentity = {
    board: "CBSE",
    grade: "10",
    subject: "History",
    unit: "India and the Contemporary World II",
    chapter_title: "Print Culture and the Modern World",
    chapter_code: "10CBSS_PrintCulture",
  };
  const onScope = vi.fn();
  render(
    <DirectoryPicker onScope={onScope} chapterOnly initialChapterIdentity={savedIdentity} />,
  );

  expect(
    await screen.findByText(
      "Saved checkpoint target selected: Print Culture and the Modern World "
      + "(saved under History, shown here under Social Science).",
    ),
  ).toBeDefined();
  expect(apiMock.resolveSavedChapter).toHaveBeenCalledWith(savedIdentity);

  const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
  expect(selects[2].value).toBe("Social Science");
  expect(selects[4].value).toBe("129");
  await waitFor(() => {
    expect(onScope).toHaveBeenLastCalledWith({
      type: "chapter",
      ids: [129],
      label: "Print Culture and the Modern World",
    });
  });
});

test("keeps generation unselected and shows the server's reason when the saved target is absent", async () => {
  apiMock.resolveSavedChapter.mockResolvedValue({
    resolved: false,
    reason:
      "A Chapter Nobody Imported is not in the directory under this board, "
      + "class and subject",
    chapter: null,
  });

  const onScope = vi.fn();
  render(
    <DirectoryPicker
      onScope={onScope}
      chapterOnly
      initialChapterIdentity={{
        board: "CBSE",
        grade: "10",
        subject: "Science",
        unit: "missing unit",
        chapter_title: "A Chapter Nobody Imported",
        chapter_code: "missing",
      }}
    />,
  );

  expect(
    await screen.findByText(
      "A Chapter Nobody Imported is not in the directory under this board, "
      + "class and subject",
    ),
  ).toBeDefined();
  // The old generic sentence said nothing about WHICH level failed.
  expect(screen.queryByText(/saved destination is not in the current directory/i))
    .toBeNull();
  expect(onScope).toHaveBeenLastCalledWith(null);
});

test("says the lookup failed and leaves the picker usable", async () => {
  apiMock.resolveSavedChapter.mockRejectedValue(
    new Error("500 Internal Server Error"),
  );

  const onScope = vi.fn();
  render(
    <DirectoryPicker
      onScope={onScope}
      chapterOnly
      initialChapterIdentity={{
        board: "CBSE",
        grade: "10",
        subject: "Science",
        unit: "Electricity and Magnetism",
        chapter_title: "Electricity",
        chapter_code: "CH-11",
      }}
    />,
  );

  expect(await screen.findByText(/could not be looked up/i)).toBeDefined();
  expect(screen.getByText(/500 Internal Server Error/)).toBeDefined();

  // Manual selection still reaches a chapter.
  await screen.findByRole("option", { name: "CBSE" });
  const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
  expect(selects[0].disabled).toBe(false);
  fireEvent.change(selects[0], { target: { value: "CBSE" } });
  fireEvent.change(selects[1], { target: { value: "10" } });
  fireEvent.change(selects[2], { target: { value: "Science" } });
  fireEvent.change(selects[3], { target: { value: "Electricity and Magnetism" } });
  fireEvent.change(selects[4], { target: { value: "77" } });
  await waitFor(() => {
    expect(onScope).toHaveBeenLastCalledWith({
      type: "chapter",
      ids: [77],
      label: "Electricity",
    });
  });
});

test("resolves once per identity and reload signal, not on every render", async () => {
  const identity = {
    board: "CBSE",
    grade: "10",
    subject: "Science",
    unit: "Electricity and Magnetism",
    chapter_title: "Electricity",
    chapter_code: "CH-11",
  };
  const view = render(
    <DirectoryPicker
      onScope={vi.fn()}
      chapterOnly
      reloadSignal={0}
      initialChapterIdentity={identity}
    />,
  );

  expect(
    await screen.findByText("Saved checkpoint target selected: Electricity."),
  ).toBeDefined();
  expect(apiMock.resolveSavedChapter).toHaveBeenCalledTimes(1);

  // Re-renders with the same identity and reload signal must not ask again.
  view.rerender(
    <DirectoryPicker
      onScope={vi.fn()}
      chapterOnly
      reloadSignal={0}
      initialChapterIdentity={{ ...identity }}
    />,
  );
  view.rerender(
    <DirectoryPicker
      onScope={vi.fn()}
      chapterOnly
      reloadSignal={0}
      initialChapterIdentity={{ ...identity }}
    />,
  );
  await waitFor(() => {
    expect(apiMock.tree).toHaveBeenCalledTimes(1);
  });
  expect(apiMock.resolveSavedChapter).toHaveBeenCalledTimes(1);

  // A reloaded directory is a new question about the same identity.
  view.rerender(
    <DirectoryPicker
      onScope={vi.fn()}
      chapterOnly
      reloadSignal={1}
      initialChapterIdentity={identity}
    />,
  );
  await waitFor(() => {
    expect(apiMock.resolveSavedChapter).toHaveBeenCalledTimes(2);
  });
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

test("styles a refusal as an error even when it starts like a success", async () => {
  // The message is written by the server and built from the saved chapter's
  // own title, so the outcome must be carried rather than sniffed from text.
  apiMock.resolveSavedChapter.mockResolvedValue({
    resolved: false,
    reason: "Saved checkpoint Chapter is not in the directory under this "
      + "board, class and subject",
    chapter: null,
  });
  render(
    <DirectoryPicker
      onScope={() => {}}
      initialChapterIdentity={{
        board: "CBSE", grade: "10", subject: "History", unit: "U",
        chapter_title: "Saved checkpoint Chapter", chapter_code: "10CBSS_X",
      }}
    />,
  );
  const note = await screen.findByText(/Saved checkpoint Chapter is not in/);
  expect(note.className).toContain("error-box");
  expect(note.className).not.toContain("resume-target-ok");
});
