import { render, screen } from "@testing-library/react";
import type { OpenAIUsage } from "../types";
import ApiUsageSummary from "./ApiUsageSummary";

const USAGE: OpenAIUsage = {
  model: "gpt-5.4-mini-2026-03-17",
  request_count: 3,
  input_tokens: 1234,
  cached_input_tokens: 234,
  uncached_input_tokens: 1000,
  output_tokens: 456,
  reasoning_tokens: 120,
  total_tokens: 1690,
  estimated_cost_usd: 0.007654,
  currency: "USD",
  pricing_complete: true,
};

test("shows token counts, model, generated file and estimated cost", () => {
  render(<ApiUsageSummary usage={USAGE} filename="chapter-workbook.pdf" />);

  expect(screen.getByText("API usage & estimated cost")).toBeDefined();
  expect(screen.getByText("Generated file: chapter-workbook.pdf")).toBeDefined();
  expect(screen.getByText("gpt-5.4-mini-2026-03-17")).toBeDefined();
  expect(screen.getByText("1,234")).toBeDefined();
  expect(screen.getByText("234")).toBeDefined();
  expect(screen.getByText("Included in input")).toBeDefined();
  expect(screen.getByText("120 reasoning included")).toBeDefined();
  expect(screen.getByText("1,690")).toBeDefined();
  expect(screen.getByText("$0.007654")).toBeDefined();
});

test("explains when token pricing is unavailable", () => {
  render(
    <ApiUsageSummary
      usage={{ ...USAGE, model: "custom-model", estimated_cost_usd: null, pricing_complete: false }}
    />,
  );

  expect(screen.getByText("Unavailable")).toBeDefined();
  expect(screen.getByText(/pricing is not configured for every model used/i)).toBeDefined();
});

test("can distinguish an uploaded source from a generated artifact", () => {
  render(
    <ApiUsageSummary
      usage={USAGE}
      filename="source-chapter.pdf"
      fileLabel="Source file"
    />,
  );

  expect(screen.getByText("Source file: source-chapter.pdf")).toBeDefined();
});

test("does not render an empty usage record", () => {
  render(
    <ApiUsageSummary
      usage={{
        ...USAGE,
        request_count: 0,
        input_tokens: 0,
        cached_input_tokens: 0,
        uncached_input_tokens: 0,
        output_tokens: 0,
        reasoning_tokens: 0,
        total_tokens: 0,
        estimated_cost_usd: 0,
      }}
    />,
  );

  expect(screen.queryByTestId("api-usage-summary")).toBeNull();
});
