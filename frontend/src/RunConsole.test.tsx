import { act, fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { StreamEvent } from "./api/client";
import { RunConsoleProvider, useRunConsole } from "./RunConsole";
import type { OpenAIUsage } from "./types";

const pending = vi.hoisted(() => [] as Array<{
  onEvent: (event: StreamEvent) => void;
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}>);

const getUploadJobMock = vi.hoisted(() => vi.fn());
const getRunEventsMock = vi.hoisted(() => vi.fn());

vi.mock("./api/client", () => ({
  streamNdjson: vi.fn(
    (_path: string, _init: RequestInit, onEvent: (event: StreamEvent) => void) =>
      new Promise((resolve, reject) => pending.push({ onEvent, resolve, reject })),
  ),
  api: { getUploadJob: getUploadJobMock, getRunEvents: getRunEventsMock },
}));

function transportError(message: string): Error {
  const failure = new Error(message);
  failure.name = "StreamTransportError";
  return failure;
}

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
      <button
        onClick={() => void run(
          "Retry",
          "/retry",
          {},
          {
            cumulative: true,
            resumed: true,
            filename: "chapter.pdf",
            initialUsage: usage(500),
          },
        )}
      >
        Retry
      </button>
      <output data-testid="usage">{state.usage?.total_tokens ?? "none"}</output>
      <output data-testid="usage-context">
        {state.usagePresentation?.cumulative ? "cumulative" : "run"}
        {state.usagePresentation?.resumed ? " resumed" : ""}
      </output>
      <output data-testid="progress-label">{state.progressLabel}</output>
      <output data-testid="status">{state.status}</output>
      <output data-testid="progress">{state.progress}</output>
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

test("a checkpoint retry starts from and preserves the cumulative file total", () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("Retry"));
  expect(screen.getByTestId("usage").textContent).toBe("500");
  expect(screen.getByTestId("usage-context").textContent).toBe(
    "cumulative resumed",
  );

  // Even if a transient event contains only this attempt's subtotal, the
  // cumulative presentation must not look like a fresh usage counter.
  act(() => pending[0].onEvent({ type: "usage", data: usage(50) }));
  expect(screen.getByTestId("usage").textContent).toBe("500");

  act(() => pending[0].onEvent({ type: "usage", data: usage(550) }));
  expect(screen.getByTestId("usage").textContent).toBe("550");
});

test("a stream heartbeat makes a long final step visibly active", () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  act(() => pending[0].onEvent({
    type: "step",
    label: "Post Learning — generating concepts",
  }));
  act(() => pending[0].onEvent({ type: "heartbeat" }));

  expect(screen.getByTestId("progress-label").textContent).toBe(
    "Post Learning — generating concepts (still working...)",
  );
});

test("an awaiting-decision result stays paused at its checkpoint", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  await act(async () => {
    pending[0].resolve({
      status: "awaiting_decision",
      pending_decision: {
        decision_id: "phase33-host-abc123",
        checkpoint_progress: 0.81,
      },
    });
  });

  expect(screen.getByTestId("status").textContent).toBe("paused");
  expect(screen.getByTestId("progress").textContent).toBe("0.81");
  expect(screen.getByTestId("progress-label").textContent).toBe(
    "Paused for your decision",
  );
});


test("a mid-run drop tails the journal silently and resumes only if the worker died", async () => {
  vi.useFakeTimers();
  try {
    // The journal has nothing new, the worker is gone (machine restart),
    // and job status says stopped-at-checkpoint: the ONE spoken case.
    getRunEventsMock.mockResolvedValue({ events: [], next: 0, running: false });
    getUploadJobMock.mockResolvedValue({
      generation_running: false, status: "converted",
    });

    function ReattachProbe() {
      const { run, state } = useRunConsole();
      return (
        <>
          <button
            onClick={() => void run(
              "Generate",
              "/generate",
              {},
              undefined,
              { module: "concepts", jobId: 7 },
            ).catch(() => undefined)}
          >
            Generate
          </button>
          <output data-testid="status">{state.status}</output>
          <output data-testid="lines">
            {state.lines.map((line) => line.message).join(" | ")}
          </output>
        </>
      );
    }
    render(
      <RunConsoleProvider>
        <ReattachProbe />
      </RunConsoleProvider>,
    );

    const first = pending.length;
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await act(async () => {
      pending[first].reject(transportError("network connection lost"));
      await Promise.resolve();
    });

    // No drop message of any kind: the console holds its state.
    expect(screen.getByTestId("status").textContent).toBe("running");
    expect(screen.getByTestId("lines").textContent).not.toContain(
      "Network connection lost",
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(getRunEventsMock).toHaveBeenCalledWith("concepts", 7, 0);
    expect(screen.getByTestId("lines").textContent).toContain(
      "Resuming the run from its saved checkpoint",
    );

    // The request was re-issued and this time it completes.
    expect(pending).toHaveLength(first + 2);
    await act(async () => {
      pending[first + 1].onEvent(
        { type: "result", data: { status: "generated" } },
      );
      pending[first + 1].resolve({ status: "generated" });
      await Promise.resolve();
    });
    expect(screen.getByTestId("status").textContent).toBe("done");
  } finally {
    vi.useRealTimers();
    pending.length = 0;
    getUploadJobMock.mockReset();
    getRunEventsMock.mockReset();
  }
});


test("a drop while the run continues catches up from the journal and finishes silently", async () => {
  vi.useFakeTimers();
  try {
    // First tail poll: the lines missed while away plus the terminal
    // result — the run FINISHES through the journal, no re-POST, and
    // not a single meta-message.
    getRunEventsMock.mockResolvedValueOnce({
      events: [
        { type: "log", level: "info", message: "missed while away", seq: 3 },
        { type: "progress", value: 0.9, label: "Polishing", seq: 4 },
        { type: "result", data: { status: "generated" }, seq: 5 },
      ],
      next: 5,
      running: false,
    });

    function ReattachProbe() {
      const { run, state } = useRunConsole();
      return (
        <>
          <button
            onClick={() => void run(
              "Generate",
              "/generate",
              {},
              undefined,
              { module: "concepts", jobId: 7 },
            ).catch(() => undefined)}
          >
            Generate
          </button>
          <output data-testid="status">{state.status}</output>
          <output data-testid="lines">
            {state.lines.map((line) => line.message).join(" | ")}
          </output>
        </>
      );
    }
    render(
      <RunConsoleProvider>
        <ReattachProbe />
      </RunConsoleProvider>,
    );

    const first = pending.length;
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));

    // Two live events arrive (seq 1-2), then the tab freeze kills the
    // stream.
    act(() => {
      pending[first].onEvent({
        type: "log", level: "info", message: "seen live", seq: 1,
      });
      pending[first].onEvent({ type: "progress", value: 0.5, seq: 2 });
    });
    await act(async () => {
      pending[first].reject(transportError("connection dropped"));
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    // The cursor asked only for what was missed…
    expect(getRunEventsMock).toHaveBeenCalledWith("concepts", 7, 2);
    // …the missed line is painted, the run is done, and the log carries
    // ZERO reconnect chatter.
    const lines = screen.getByTestId("lines").textContent ?? "";
    expect(lines).toContain("seen live");
    expect(lines).toContain("missed while away");
    expect(lines).not.toContain("Network connection lost");
    expect(lines).not.toContain("Re-attach");
    expect(lines).not.toContain("Resuming");
    expect(screen.getByTestId("status").textContent).toBe("done");
    expect(getUploadJobMock).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
    pending.length = 0;
    getUploadJobMock.mockReset();
    getRunEventsMock.mockReset();
  }
});
