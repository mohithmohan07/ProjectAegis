import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SourceBookInput from "./SourceBookInput";

test("explains publication source ownership for post and pre questions", () => {
  render(<SourceBookInput value="Seed to Plant" onChange={vi.fn()} options={[]} />);

  expect(screen.getByDisplayValue("Seed to Plant")).toBeDefined();
  expect(screen.getByText(
    /selected publication is used as the Concept Source and the extracted Post-Learning Question Source/i,
  )).toBeDefined();
  expect(screen.getByText(
    /Generated Pre-Learning questions use UpSchool DB as their Question Source/i,
  )).toBeDefined();
});
