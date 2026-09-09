import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { RunState } from "../RunConsole";
import RunConsolePanel from "./RunConsolePanel";
import { downloadConsoleSnapshot } from "../lib/runSnapshot";

const mocks = vi.hoisted(() => ({ state: {} as RunState, clear: vi.fn() }));
vi.mock("../RunConsole", () => ({
  useRunConsole: () => ({ state: mocks.state, setOpen: vi.fn(), clear: mocks.clear }),
}));
vi.mock("../lib/runSnapshot", () => ({ downloadConsoleSnapshot: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.state = {
    active: true, open: true, title: "Statistics", status: "running",
    progress: 0.96, progressLabel: "Master marking", startedAt: 100,
    lines: [
      { level: "step", message: "Master marking", ts: 100 },
      { level: "info", message: "Review received", ts: 110, lane: "Pre" },
    ],
    usagePresentation: { filename: "statistics.pdf", cumulative: true },
    usage: {
      model: "test-model", request_count: 2, provider_request_count: 4,
      pending_request_count: 1, unresolved_usage_request_count: 1,
      usage_complete: false, input_tokens: 80, output_tokens: 20,
      cached_input_tokens: 0, uncached_input_tokens: 80, reasoning_tokens: 5,
      total_tokens: 100, estimated_cost_usd: null,
      known_usage_estimated_cost_usd: 0.032,
      estimated_cost_inr: null, known_usage_estimated_cost_inr: 2.88,
      inr_conversion_complete: true,
      stages: [{
        stage: "Master marking", lane: "Pre", request_count: 2, provider_request_count: 4,
        pending_request_count: 1, unresolved_usage_request_count: 1, usage_complete: false,
        input_tokens: 80, output_tokens: 20, total_tokens: 100, reasoning_tokens: 5,
        estimated_cost_usd: null, known_usage_estimated_cost_usd: 0.032,
        estimated_cost_inr: null, known_usage_estimated_cost_inr: 2.88,
        inr_conversion_complete: true,
        pricing_complete: true, first_ts: 100, last_ts: 110,
      }],
    },
  };
});

test("live console, stages and lanes show recorded INR with incomplete usage and pending requests", () => {
  render(<RunConsolePanel />);
  const summary = screen.getByText(/Model usage \(live — still accumulating\)/);
  expect(summary.textContent).toContain("₹2.88 recorded");
  expect(summary.textContent).toContain("1 pending");
  expect(summary.textContent).toContain("Usage incomplete");
  const stage = screen.getByText("Master marking", { selector: ".stage-title" })
    .closest("summary")!;
  expect(stage.textContent).toContain("₹2.88 recorded");
  expect(stage.textContent).toContain("1 pending");
  expect(stage.textContent).toContain("Usage incomplete");
  const lane = document.querySelector(".stage-lane-cost")!;
  expect(lane.textContent).toContain("₹2.88 recorded");
  expect(lane.textContent).toContain("1 pending");
  expect(lane.textContent).toContain("Usage incomplete");
});

test("usage details start folded, toggle accessibly, and keep the choice across usage updates", () => {
  const { rerender } = render(<RunConsolePanel />);
  const show = screen.getByRole("button", { name: /show usage details/i });
  expect(show.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Input tokens", { selector: "dt" })).toBeNull();

  fireEvent.click(show);
  expect(screen.getByRole("button", { name: /hide usage details/i })
    .getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Input tokens", { selector: "dt" })).toBeDefined();

  mocks.state = {
    ...mocks.state,
    usage: { ...mocks.state.usage!, total_tokens: 101 },
  };
  rerender(<RunConsolePanel />);
  expect(screen.getByRole("button", { name: /hide usage details/i })
    .getAttribute("aria-expanded")).toBe("true");

  fireEvent.click(screen.getByRole("button", { name: /hide usage details/i }));
  expect(screen.getByRole("button", { name: /show usage details/i })
    .getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Input tokens", { selector: "dt" })).toBeNull();
});

test("historical USD remains visible while INR is explicitly unavailable", () => {
  const usage = mocks.state.usage!;
  for (const record of [usage, ...(usage.stages ?? [])]) {
    delete record.estimated_cost_inr;
    delete record.known_usage_estimated_cost_inr;
    delete record.inr_conversion_complete;
  }
  render(<RunConsolePanel />);

  const summary = screen.getByText(/Model usage \(live — still accumulating\)/);
  expect(summary.textContent).toContain("INR unavailable ($0.0320 recorded)");
  const stage = screen.getByText("Master marking", { selector: ".stage-title" })
    .closest("summary")!;
  expect(stage.textContent).toContain("INR unavailable ($0.0320 recorded)");
  expect(document.querySelector(".stage-lane-cost")!.textContent)
    .toContain("INR unavailable ($0.0320 recorded)");
});

test("a partially converted stage labels its INR subtotal and retains the full USD amount", () => {
  mocks.state.usage!.stages!.push({
    stage: "Master marking", lane: "Post", request_count: 1,
    provider_request_count: 1, input_tokens: 30, output_tokens: 10,
    total_tokens: 40, reasoning_tokens: 0, estimated_cost_usd: 0.01,
    usage_complete: true, pricing_complete: true, first_ts: 100, last_ts: 110,
  });
  render(<RunConsolePanel />);

  const stage = screen.getByText("Master marking", { selector: ".stage-title" })
    .closest("summary")!;
  expect(stage.textContent).toContain("₹2.88 recorded ($0.0420 recorded)");
  expect(stage.textContent).toContain("INR conversion incomplete");
  expect(stage.textContent).toContain("1 pending");
  expect(stage.textContent).toContain("Usage incomplete");
});

test("download remains enabled during a run and includes state outside the selected log filter", () => {
  render(<RunConsolePanel />);
  fireEvent.click(screen.getByRole("button", { name: "Raw" }));
  fireEvent.click(screen.getByRole("button", { name: "Issues" }));
  expect(screen.getByText("No lines match this filter yet.")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Download snapshot" }));
  expect(downloadConsoleSnapshot).toHaveBeenCalledWith(mocks.state);
  expect(mocks.clear).not.toHaveBeenCalled();
  expect(mocks.state.active).toBe(true);
});
