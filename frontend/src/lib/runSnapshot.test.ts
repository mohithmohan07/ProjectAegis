import { consoleSnapshot } from "./runSnapshot";
import type { RunState } from "../RunConsole";

test("a live snapshot preserves retained timestamps, lanes and partial usage with its scope explicit", () => {
  const state: RunState = {
    active: true, open: true, title: "Statistics", status: "running",
    progress: 0.96, progressLabel: "Master marking", startedAt: 100,
    lines: [
      { ts: 200, level: "step", message: "Master marking" },
      { ts: 205, level: "info", message: "Review complete", lane: "Pre" },
    ],
    usagePresentation: { filename: "statistics.pdf", cumulative: true, resumed: true },
    usage: {
      model: "test-model", request_count: 2, provider_request_count: 4,
      pending_request_count: 1, unresolved_usage_request_count: 1,
      usage_complete: false, input_tokens: 80, output_tokens: 20,
      cached_input_tokens: 0, uncached_input_tokens: 80, reasoning_tokens: 5,
      total_tokens: 100, estimated_cost_usd: null,
      known_usage_estimated_cost_usd: 0.03,
    },
  };
  const result = JSON.parse(JSON.stringify(consoleSnapshot(state, new Date("2026-09-09T11:00:00Z"))));
  expect(result.captured_at).toBe("2026-09-09T11:00:00.000Z");
  expect(result.run).toMatchObject({ active: true, status: "running", progress: 0.96,
    source_filename: "statistics.pdf", cumulative_usage: true, resumed: true });
  expect(result.usage).toEqual(state.usage);
  expect(result.log.events).toEqual(state.lines);
  expect(result.log).toMatchObject({ retained_event_count: 2, first_retained_event_ts: 200,
    last_retained_event_ts: 205, timestamp_unit: "epoch_seconds" });
  expect(result.scope).toContain("bounded log");
  expect(result.scope).toContain("not the full server diagnostics bundle");
  expect(result.scope).toContain("may lag the running job");
});
