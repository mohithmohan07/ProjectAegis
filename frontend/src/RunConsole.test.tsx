import { act, fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { StreamEvent } from "./api/client";
import { RunConsoleProvider, useRunConsole } from "./RunConsole";
import type { OpenAIUsage } from "./types";

const pending = vi.hoisted(() => [] as Array<{
  onEvent: (event: StreamEvent) => void;
  resolve: (value: unknown) => void;
}>);

vi.mock("./api/client", () => ({
  streamNdjson: vi.fn(
    (_path: string, _init: RequestInit, onEvent: (event: StreamEvent) => void) =>
      new Promise((resolve) => pending.push({ onEvent, resolve })),
  ),
}));

function usage(totalTokens: number): OpenAIUsage {
  return {
    model: "gpt-5.4-mini-2026-03-17",
    request_count: 1,
    input_tokens: totalTokens - 10,
    cached_input_tokens: 0,
    uncached_input_tokens: totalTokens - 10,
    output_tokens: 10,
    reasoning_tokens: 0,
    total_tokens: totalTokens,
    estimated_cost_usd: 0.001,
  };
}

function Probe() {
  const { run, state } = useRunConsole();
  return (
    <>
      <button onClick={() => void run("First", "/first")}>First</button>
      <button onClick={() => void run("Second", "/second")}>Second</button>
      <output data-testid="usage">{state.usage?.total_tokens ?? "none"}</output>
    </>
  );
}

test("ignores late usage events from an older overlapping run", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  act(() => pending[0].onEvent({ type: "usage", data: usage(111) }));
  expect(screen.getByTestId("usage").textContent).toBe("111");

  fireEvent.click(screen.getByText("Second"));
  expect(screen.getByTestId("usage").textContent).toBe("none");

  act(() => pending[0].onEvent({ type: "usage", data: usage(999) }));
  expect(screen.getByTestId("usage").textContent).toBe("none");

  act(() => pending[1].onEvent({ type: "usage", data: usage(222) }));
  expect(screen.getByTestId("usage").textContent).toBe("222");

  await act(async () => {
    pending[0].resolve({});
    pending[1].resolve({});
  });
});
