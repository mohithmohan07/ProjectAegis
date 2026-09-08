import { render, screen, within } from "@testing-library/react";
import type { OpenAIUsage } from "../types";
import ApiUsageSummary from "./ApiUsageSummary";

const USAGE: OpenAIUsage = {
  model: "gpt-5.4-mini-2026-03-17",
  request_count: 3,
  input_tokens: 1234,
  cached_input_tokens: 234,
  cache_write_tokens: 12,
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
  expect(screen.getAllByText("Included in input")).toHaveLength(2);
  expect(screen.getByText("Cache writes")).toBeDefined();
  expect(screen.getByText("12")).toBeDefined();
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

test("labels checkpoint-recovered file usage as resumed and cumulative", () => {
  render(
    <ApiUsageSummary
      usage={USAGE}
      filename="source-chapter.pdf"
      fileLabel="Source file"
      cumulative
      resumed
    />,
  );

  expect(
    screen.getByText("Cumulative API usage & estimated cost"),
  ).toBeDefined();
  expect(screen.getByText("Resumed")).toBeDefined();
  expect(screen.getByText(
    /resumed from a saved checkpoint/i,
  )).toBeDefined();
  expect(screen.getByText(
    /retrying does not reset them/i,
  )).toBeDefined();
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

test("does not render a sparse empty API usage object", () => {
  render(<ApiUsageSummary usage={{} as OpenAIUsage} cumulative />);
  expect(screen.queryByTestId("api-usage-summary")).toBeNull();
});

const ZERO_USAGE: OpenAIUsage = {
  ...USAGE,
  request_count: 0,
  input_tokens: 0,
  cached_input_tokens: 0,
  cache_write_tokens: 0,
  uncached_input_tokens: 0,
  output_tokens: 0,
  reasoning_tokens: 0,
  total_tokens: 0,
  estimated_cost_usd: 0,
};

test("shows attempts without usage receipts and never presents their unknown cost as free", () => {
  render(<ApiUsageSummary usage={{
    ...ZERO_USAGE,
    provider_request_count: 2,
    attempt_count: 3,
    usage_complete: false,
    missing_usage_response_count: 1,
    stages: [{
      stage: "Rubric review", lane: "Post", request_count: 0,
      provider_request_count: 2, attempt_count: 3, usage_complete: false,
      input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, total_tokens: 0,
      estimated_cost_usd: 0, pricing_complete: true, first_ts: 1, last_ts: 1,
    }],
  }} />);

  expect(screen.getByTestId("api-usage-summary")).toBeDefined();
  expect(screen.getByText("Requests", { selector: "dt" }).parentElement?.textContent)
    .toContain("2");
  expect(screen.getByText("Attempts", { selector: "dt" }).parentElement?.textContent)
    .toContain("3");
  expect(screen.getByText("Estimated cost", { selector: "dt" }).parentElement?.textContent)
    .toContain("Unavailable");
  expect(screen.queryByText("$0.000000")).toBeNull();
  expect(screen.getByText(/token usage is missing for one or more provider requests/i))
    .toBeDefined();
  const stageRow = screen.getByText("Rubric review").closest("tr")!;
  const cells = within(stageRow).getAllByRole("cell");
  expect(cells[1].textContent).toBe("2");
  expect(cells[3].textContent).toBe("Unavailable");
});

test("shows elapsed and local processing time for a zero-API checkpoint replay", () => {
  render(<ApiUsageSummary resumed usage={{
    ...ZERO_USAGE,
    provider_request_count: 0,
    attempt_count: 0,
    usage_complete: true,
    elapsed_seconds: 120,
    mechanical_wall_seconds: 90,
    mechanical_span_count: 2,
  }} />);

  expect(screen.getByTestId("api-usage-summary")).toBeDefined();
  expect(screen.getByText("Resumed")).toBeDefined();
  expect(screen.getByText("Time taken", { selector: "dt" }).parentElement?.textContent)
    .toContain("2m 0s");
  expect(screen.getByText("Local processing", { selector: "dt" }).parentElement?.textContent)
    .toContain("1m 30s");
  expect(screen.getByText("Requests", { selector: "dt" }).parentElement?.textContent)
    .toBe("Requests0");
  expect(screen.queryByText(/token usage is missing/i)).toBeNull();
});
