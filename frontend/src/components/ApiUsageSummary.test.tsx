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
  estimated_cost_inr: 0.65,
  inr_conversion_complete: true,
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
  expect(screen.getByText("₹0.6500")).toBeDefined();
  expect(screen.getByText("$0.007654 USD")).toBeDefined();
});

test("explains when token pricing is unavailable", () => {
  render(
    <ApiUsageSummary
      usage={{ ...USAGE, model: "custom-model", estimated_cost_usd: null, pricing_complete: false }}
    />,
  );

  expect(screen.getByText("Unavailable")).toBeDefined();
  expect(screen.getByText(/pricing is not configured for every model or service tier used/i)).toBeDefined();
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
        estimated_cost_inr: 0,
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
  estimated_cost_inr: 0,
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
  expect(cells[3].textContent).toBe("Unavailable · Usage incomplete");
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

test("shows active processing, review waiting and total wall time separately", () => {
  render(<ApiUsageSummary usage={{
    ...ZERO_USAGE,
    active_elapsed_seconds: 20,
    review_wait_seconds: 100,
    wall_elapsed_seconds: 120,
  }} cumulative />);

  expect(screen.getByText("Time taken", { selector: "dt" }).parentElement?.textContent)
    .toContain("20s");
  expect(screen.getByText("Review waiting", { selector: "dt" }).parentElement?.textContent)
    .toContain("1m 40s");
  expect(screen.getByText("Total wall time", { selector: "dt" }).parentElement?.textContent)
    .toContain("2m 0s");
});

test("retains recorded cost while parallel requests are pending without calling their usage missing", () => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE,
    provider_request_count: 5,
    pending_request_count: 2,
    unresolved_usage_request_count: 0,
    usage_complete: true,
  }} />);
  expect(screen.getByText("Recorded estimate", { selector: "dt" }).parentElement?.textContent)
    .toContain("₹0.6500");
  expect(screen.getByText(/2 provider requests are still running/)).toBeDefined();
  expect(screen.queryByText(/token usage is missing/i)).toBeNull();
  expect(screen.queryByText("Unavailable")).toBeNull();
});

test("retains known rupees after missing usage without presenting them as the full bill", () => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE,
    provider_request_count: 5,
    pending_request_count: 1,
    unresolved_usage_request_count: 1,
    usage_complete: false,
    estimated_cost_usd: null,
    known_usage_estimated_cost_usd: 0.032,
    known_usage_estimated_cost_inr: 2.88,
  }} />);
  expect(screen.getByText("Recorded estimate", { selector: "dt" }).parentElement?.textContent)
    .toContain("₹2.88");
  expect(screen.getByText(/1 provider request is still running/)).toBeDefined();
  expect(screen.getByText(/full total is unknown/)).toBeDefined();
  expect(screen.queryByText("Estimated cost", { selector: "dt" })).toBeNull();
  expect(screen.queryByText(/pricing is not configured/i)).toBeNull();
});

test.each([0, 1])("new request-state counters expose missing usage and pricing together (unresolved=%i)", (unresolved) => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE,
    provider_request_count: 4,
    pending_request_count: 0,
    unresolved_usage_request_count: unresolved,
    usage_complete: false,
    pricing_complete: false,
    estimated_cost_usd: null,
    known_usage_estimated_cost_usd: 0.032,
    known_usage_estimated_cost_inr: 2.88,
  }} />);
  expect(screen.getByText("₹2.88")).toBeDefined();
  expect(screen.getByText(/exclude unresolved charges/)).toBeDefined();
  expect(screen.getByText(/exclude unpriced usage/)).toBeDefined();
  expect(screen.queryByText(/provider requests? (is|are) still running/)).toBeNull();
});

test("legacy missing usage does not falsely diagnose absent model rates", () => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE,
    provider_request_count: 4,
    usage_complete: false,
    pricing_complete: false,
    estimated_cost_usd: null,
    known_usage_estimated_cost_usd: 0.032,
    estimated_cost_inr: undefined,
    inr_conversion_complete: undefined,
  }} />);
  expect(screen.getByText("$0.0320 USD recorded")).toBeDefined();
  expect(screen.getByText("Unavailable")).toBeDefined();
  expect(screen.getByText(/exclude unresolved charges/)).toBeDefined();
  expect(screen.queryByText(/pricing is not configured/i)).toBeNull();
});

test("compact usage retains the priced subtotal and explains an unknown rate", () => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE,
    estimated_cost_usd: null,
    known_usage_estimated_cost_usd: 0.015,
    known_usage_estimated_cost_inr: 1.35,
    pricing_complete: false,
  }} />);
  expect(screen.getByText("₹1.35")).toBeDefined();
  expect(screen.getByText(/exclude unpriced usage/)).toBeDefined();
  expect(screen.queryByText(/token usage is missing/i)).toBeNull();
});

test("stage rows preserve their known cost and request state", () => {
  render(<ApiUsageSummary usage={{
    ...USAGE,
    stages: [{
      stage: "Master marking", lane: "Pre", request_count: 3,
      provider_request_count: 5, pending_request_count: 1,
      unresolved_usage_request_count: 1, usage_complete: false,
      input_tokens: 100, output_tokens: 20, reasoning_tokens: 10, total_tokens: 120,
      estimated_cost_usd: null, known_usage_estimated_cost_usd: 0.032,
      estimated_cost_inr: null, known_usage_estimated_cost_inr: 2.88,
      inr_conversion_complete: true,
      pricing_complete: true, first_ts: 1, last_ts: 2,
    }],
  }} />);
  const costCell = within(screen.getByText("Master marking").closest("tr")!)
    .getAllByRole("cell")[3];
  expect(costCell.textContent).toBe("₹2.88 recorded · 1 pending · Usage incomplete");
});

test("Gemini receipts show individual and cumulative INR without recalculating old rates", () => {
  const request = {
    attempt_id: "gemini-call-1", requested_model: "gemini-3.1-pro-preview",
    actual_model: "gemini-3.1-pro-preview", stage: "Question review", lane: "Post",
    outcome: "response_received", usage_reported: true, total_tokens: 1200,
    estimated_cost_usd: 0.01, estimated_cost_inr: 0.875,
    usd_to_inr_rate: "87.5", usd_to_inr_as_of: "2026-09-08",
  };
  render(<ApiUsageSummary cumulative usage={{
    ...USAGE, model: "multiple", estimated_cost_usd: 0.03,
    estimated_cost_inr: 2.735, usd_to_inr_rate: null,
    latest_request: request, request_attempts: [request, {
      ...request, attempt_id: "luna-call-2", actual_model: "gpt-6-luna",
      estimated_cost_usd: 0.02, estimated_cost_inr: 1.86,
      usd_to_inr_rate: "93", usd_to_inr_as_of: "2026-09-09",
    }],
  }} />);

  expect(screen.getByText("Estimated cost", { selector: "dt" }).parentElement?.textContent)
    .toContain("₹2.74");
  expect(screen.getByText("Latest request", { selector: "dt" }).parentElement?.textContent)
    .toContain("₹0.8750");
  const table = screen.getByRole("table", { name: "Individual provider request costs" });
  expect(within(table).getByText("gemini-3.1-pro-preview")).toBeDefined();
  expect(within(table).getByText("₹0.8750")).toBeDefined();
  expect(within(table).getByText("₹1.86")).toBeDefined();
});

test("partial INR coverage keeps the converted subtotal explicitly recorded", () => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE, estimated_cost_usd: 0.132, estimated_cost_inr: null,
    known_usage_estimated_cost_inr: 2.88, inr_conversion_complete: false,
  }} />);
  expect(screen.getByText("Recorded estimate", { selector: "dt" }).parentElement?.textContent)
    .toContain("₹2.88");
  expect(screen.getByText(/INR conversion is unavailable/)).toBeDefined();
  expect(screen.getByText("$0.1320 USD")).toBeDefined();
});

test("legacy USD is retained without being relabelled as INR or repriced at a later rate", () => {
  render(<ApiUsageSummary compact usage={{
    ...USAGE, estimated_cost_inr: undefined, inr_conversion_complete: undefined,
    usd_to_inr_rate: "100",
  }} />);
  expect(screen.getByText("Unavailable")).toBeDefined();
  expect(screen.getByText("$0.007654 USD")).toBeDefined();
  expect(screen.queryByText(/₹/)).toBeNull();
});
