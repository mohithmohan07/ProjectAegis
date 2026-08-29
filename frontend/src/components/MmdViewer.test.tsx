import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MmdViewer from "./MmdViewer";

const LONG_TEXT = `# Chapter Alpha\n${"body line with formula\n".repeat(100)}END-MARKER`;

describe("MmdViewer", () => {
  it("collapses long text but never hides it — expand shows every character", () => {
    render(<MmdViewer text={LONG_TEXT} filename="ch.pdf" />);
    // Collapsed: the tail is not rendered, and the hint says the stored
    // text is complete (the old 800-char preview looked like data loss).
    expect(screen.queryByText(/END-MARKER/)).toBeNull();
    expect(screen.getByText(/stored text is\s+complete/)).toBeTruthy();
    expect(
      screen.getByText(new RegExp(`${LONG_TEXT.length.toLocaleString()} characters`)),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show full text" }));
    expect(screen.getByText(/END-MARKER/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Collapse" })).toBeTruthy();
  });

  it("search reads the whole text and reports the match count", () => {
    render(<MmdViewer text={LONG_TEXT} filename="ch.pdf" />);
    const box = screen.getByLabelText("Search the converted text");
    fireEvent.change(box, { target: { value: "END-MARKER" } });
    // The match lives beyond the collapsed head — searching must find it
    // without the user pressing expand first.
    expect(screen.getByText("1 match")).toBeTruthy();
    fireEvent.change(box, { target: { value: "formula" } });
    expect(screen.getByText("100 matches")).toBeTruthy();
    fireEvent.change(box, { target: { value: "absent-needle" } });
    expect(screen.getByText("no matches")).toBeTruthy();
  });

  it("short text renders whole with no truncation hint", () => {
    render(<MmdViewer text="# Tiny chapter" filename="t.pdf" />);
    expect(screen.getByText(/# Tiny chapter/)).toBeTruthy();
    expect(screen.queryByText(/Showing the first/)).toBeNull();
  });
});
