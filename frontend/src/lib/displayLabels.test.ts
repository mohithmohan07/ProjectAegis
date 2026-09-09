import { expect, test } from "vitest";
import { displayLabel, stripInternalTitleTag } from "./displayLabels";

test("removes workbook identity tags while preserving ordinary parentheses", () => {
  expect(stripInternalTitleTag("Thank You Taffy (01_English_NCF)"))
    .toBe("Thank You Taffy");
  expect(stripInternalTitleTag("Prose (01_NCF)"))
    .toBe("Prose");
  expect(stripInternalTitleTag("Seasons(01_EVS_NCF)"))
    .toBe("Seasons");
  expect(stripInternalTitleTag("Similarity (06CBSC_T01_Similarity)"))
    .toBe("Similarity");
  expect(stripInternalTitleTag("A poem (poem)"))
    .toBe("A poem (poem)");
  expect(stripInternalTitleTag("A chapter (Part1)"))
    .toBe("A chapter (Part1)");
  expect(stripInternalTitleTag("A chapter (Part_1)"))
    .toBe("A chapter (Part_1)");
});

test("prefers the clean display name and never falls back to a machine ID", () => {
  expect(displayLabel(
    "Thank You Taffy",
    "Thank You Taffy (01_English_NCF)",
    "Chapter",
  )).toBe("Thank You Taffy");
  expect(displayLabel(
    "",
    "01XXEN_ThankYouTaffy",
    "Chapter",
  )).toBe("Chapter");
  expect(displayLabel("", "(01_English_NCF)", "Chapter")).toBe("Chapter");
  expect(displayLabel(undefined, undefined, "Topic")).toBe("Topic");
});
