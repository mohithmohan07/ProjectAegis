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
      stages: [{
        stage: "Master marking", lane: "Pre", request_count: 2, provider_request_count: 4,
        pending_request_count: 1, unresolved_usage_request_count: 1, usage_complete: false,
        input_tokens: 80, output_tokens: 20, total_tokens: 100, reasoning_tokens: 5,
        estimated_cost_usd: null, known_usage_estimated_cost_usd: 0.032,
        pricing_complete: true, first_ts: 100, last_ts: 110,
      }],
    },
  };
});

test("live console and stage cards keep known cost visible with incomplete usage and pending requests", () => {
  render(<RunConsolePanel />);
  const summary = screen.getByText(/Model usage \(live — still accumulating\)/);
  expect(summary.textContent).toContain("$0.0320 recorded");
  expect(summary.textContent).toContain("1 pending");
  expect(summary.textContent).toContain("Usage incomplete");
  const stage = screen.getByText("Master marking", { selector: ".stage-title" })
    .closest("summary")!;
  expect(stage.textContent).toContain("$0.0320 recorded");
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
