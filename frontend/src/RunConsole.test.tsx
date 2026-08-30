import { act, fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import type { StreamEvent } from "./api/client";
import { RunConsoleProvider, useRunConsole } from "./RunConsole";
import RunConsolePanel from "./components/RunConsolePanel";
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
  isNonTransientStatus: (error: unknown) => {
    const status = (error as { status?: number } | null)?.status;
    return (
      status === 401 || status === 403 || status === 404 || status === 410
    );
  },
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

function usageWithStage(totalTokens: number, stage: string): OpenAIUsage {
  return {
    ...usage(totalTokens),
    stages: [{
      stage,
      lane: "",
      request_count: 1,
      input_tokens: totalTokens - 10,
      output_tokens: 10,
      reasoning_tokens: 0,
      total_tokens: totalTokens,
      estimated_cost_usd: 0.001,
      pricing_complete: true,
      first_ts: 1,
      last_ts: 2,
    }],
  };
}

function Probe() {
  const { run, watch, state } = useRunConsole();
  return (
    <>
      <button
        onClick={() => void watch("Watch", { module: "concepts", jobId: 7 })
          .catch(() => undefined)}
      >
        Watch
      </button>
      <output data-testid="line-count">{state.lines.length}</output>
      <output data-testid="console-lines">
        {state.lines.map((line) => line.message).join(" | ")}
      </output>
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
            initialUsage: usageWithStage(500, "Previous attempt"),
          },
        )}
      >
        Retry
      </button>
      <output data-testid="usage">{state.usage?.total_tokens ?? "none"}</output>
      <output data-testid="usage-stages">
        {state.usage?.stages?.map((row) => row.stage).join(" | ") ?? ""}
      </output>
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
  // Stage rows are cumulative across attempts (one ledger, owner request
  // 2026-08-28): durable initial usage keeps its stage table.
  expect(screen.getByTestId("usage-stages").textContent).toBe(
    "Previous attempt",
  );
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

test("watch tails the journal to the result without POSTing anything", async () => {
  pending.length = 0;
  getRunEventsMock.mockReset();
  getRunEventsMock.mockResolvedValueOnce({
    events: [
      { type: "step", label: "Concept extraction", ts: 100, seq: 1 },
      { type: "log", level: "info", message: "working", ts: 101, seq: 2 },
      { type: "usage", data: usage(300), ts: 102, seq: 3 },
      { type: "result", data: { ok: true }, ts: 103, seq: 4 },
    ],
    next: 4,
    running: false,
  });
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  await act(async () => {
    fireEvent.click(screen.getByText("Watch"));
  });

  // Attach-only: the journal was tailed, the stream POST was never made.
  expect(pending.length).toBe(0);
  expect(getRunEventsMock).toHaveBeenCalledWith("concepts", 7, 0);
  expect(screen.getByTestId("status").textContent).toBe("done");
  expect(screen.getByTestId("usage").textContent).toBe("300");
  expect(Number(screen.getByTestId("line-count").textContent)).toBe(2);
});

test("watch reports a stopped worker and never resumes it", async () => {
  pending.length = 0;
  getRunEventsMock.mockReset();
  getUploadJobMock.mockReset();
  getRunEventsMock.mockResolvedValueOnce({
    events: [
      { type: "step", label: "Concept extraction", ts: 100, seq: 1 },
    ],
    next: 1,
    running: false,
  });
  getUploadJobMock.mockResolvedValueOnce({
    generation_running: false,
    status: "converted",
  });
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  await act(async () => {
    fireEvent.click(screen.getByText("Watch"));
  });

  expect(pending.length).toBe(0);
  expect(screen.getByTestId("status").textContent).toBe("paused");
  expect(screen.getByTestId("progress-label").textContent).toContain(
    "watching never restarts a run",
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

test("a non-resumable incomplete result names the new-upload recovery", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  await act(async () => {
    pending[0].resolve({
      status: "released",
      run_incomplete: {
        resume_allowed: false,
        recovery_action: "reconvert_new_upload",
        recovery: "Do not resume this checkpoint. Start a new upload and conversion.",
      },
    });
  });

  expect(screen.getByTestId("status").textContent).toBe("error");
  expect(screen.getByTestId("progress-label").textContent).toBe(
    "Incomplete — new upload and conversion required",
  );
  expect(screen.getByTestId("console-lines").textContent).toContain(
    "Do not resume this checkpoint. Start a new upload and conversion.",
  );
  expect(screen.getByTestId("console-lines").textContent).not.toContain(
    "resume from the saved checkpoint",
  );
});

test("an ordinary incomplete result retains the checkpoint-resume action", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  await act(async () => {
    pending[0].resolve({
      status: "released",
      run_incomplete: {
        resume_allowed: true,
        recovery_action: "resume_checkpoint",
        resume: "Resume from the saved checkpoint to finish.",
      },
    });
  });

  expect(screen.getByTestId("status").textContent).toBe("error");
  expect(screen.getByTestId("progress-label").textContent).toBe(
    "Incomplete — resume to finish",
  );
  expect(screen.getByTestId("console-lines").textContent).toContain(
    "Resume from the saved checkpoint to finish.",
  );
});

test("a missing Master corrects a premature 100% to an explicit 3/4 result", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  act(() => pending[0].onEvent({
    type: "progress",
    value: 1,
    label: "Done",
  }));
  await act(async () => {
    pending[0].resolve({
      status: "released",
      all_four_outputs_ready: false,
      master_outputs: {
        pre: { ready: true },
        post: { ready: false },
      },
      output_completion: {
        ready_count: 3,
        total_count: 4,
        all_ready: false,
        missing: [{
          number: "04",
          lane: "post",
          label: "Post-Learning Master File",
        }],
      },
    });
  });

  expect(screen.getByTestId("status").textContent).toBe("error");
  expect(screen.getByTestId("progress").textContent).toBe("0.99");
  expect(screen.getByTestId("progress-label").textContent).toBe(
    "Incomplete — 3/4 outputs ready (Post-Learning Master File unavailable)",
  );
  expect(screen.getByTestId("console-lines").textContent).toContain(
    "do not rerun Concept generation",
  );
});

test("100% is reserved for the explicit all-four-outputs verdict", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  await act(async () => {
    pending[0].resolve({
      status: "released",
      all_four_outputs_ready: true,
      master_outputs: {
        pre: { ready: true },
        post: { ready: true },
      },
      output_completion: {
        ready_count: 4,
        total_count: 4,
        all_ready: true,
        missing: [],
      },
    });
  });

  expect(screen.getByTestId("status").textContent).toBe("done");
  expect(screen.getByTestId("progress").textContent).toBe("1");
  expect(screen.getByTestId("progress-label").textContent).toBe(
    "Done — all four outputs ready",
  );
});

test("the terminal result replaces a stale live subtotal with final Master usage", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  act(() => pending[0].onEvent({ type: "usage", data: usage(600) }));
  expect(screen.getByTestId("usage").textContent).toBe("600");

  // Recovery after a frozen mobile tab can resolve directly from the job;
  // there need not be a separate final stream event for the client to apply.
  await act(async () => {
    pending[0].resolve({
      status: "released",
      openai_usage: usage(900),
      output_completion: {
        ready_count: 4,
        total_count: 4,
        all_ready: true,
        missing: [],
      },
    });
  });

  expect(screen.getByTestId("usage").textContent).toBe("900");
  expect(screen.getByTestId("status").textContent).toBe("done");
});

test("the usage summary says when cost is live and when it is final", async () => {
  pending.length = 0;
  render(
    <RunConsoleProvider>
      <Probe />
      <RunConsolePanel />
    </RunConsoleProvider>,
  );

  fireEvent.click(screen.getByText("First"));
  act(() => pending[0].onEvent({ type: "usage", data: usage(600) }));
  expect(screen.getByText(/Model usage \(live — still accumulating\)/)).toBeTruthy();

  await act(async () => {
    pending[0].resolve({
      status: "released",
      openai_usage: usage(900),
      output_completion: {
        ready_count: 4,
        total_count: 4,
        all_ready: true,
        missing: [],
      },
    });
  });

  expect(screen.getByText(/Model usage \(final for this run\)/)).toBeTruthy();
  expect(screen.getByText(/900 tokens/)).toBeTruthy();
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


test("a checkpoint re-POST resets the seq cursor so the resumed run's events apply", async () => {
  vi.useFakeTimers();
  try {
    getRunEventsMock.mockResolvedValue({ events: [], next: 0, running: false });
    getUploadJobMock.mockResolvedValue({
      generation_running: false, status: "converted",
    });

    function ResumeSeqProbe() {
      const { run, state } = useRunConsole();
      return (
        <>
          <button
            onClick={() => void run(
              "Generate",
              "/generate",
              {},
              undefined,
              { module: "concepts", jobId: 9 },
            ).catch(() => undefined)}
          >
            Generate
          </button>
          <output data-testid="lines">
            {state.lines.map((line) => line.message).join(" | ")}
          </output>
          <output data-testid="usage-stages">
            {state.usage?.stages?.map((row) => row.stage).join(" | ") ?? ""}
          </output>
          <output data-testid="status">{state.status}</output>
        </>
      );
    }
    render(
      <RunConsoleProvider>
        <ResumeSeqProbe />
      </RunConsoleProvider>,
    );

    const first = pending.length;
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await act(async () => {
      // The dead run got as far as seq 3 before the worker died.
      pending[first].onEvent({
        type: "usage", data: usageWithStage(300, "Dead attempt"), seq: 2,
      });
      pending[first].onEvent({
        type: "log", level: "info", message: "before the crash", seq: 3,
      });
      pending[first].reject(transportError("network connection lost"));
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(screen.getByTestId("lines").textContent).toContain(
      "Resuming the run from its saved checkpoint",
    );
    // The cumulative ledger keeps the dead attempt's stage rows; the
    // resumed stream's first usage event replaces them with the merged
    // server table.
    expect(screen.getByTestId("usage-stages").textContent).toBe(
      "Dead attempt",
    );

    // The re-POSTed stream is a NEW journal: seq restarts at 1. Before
    // the cursor reset, every resumed event was <= the dead run's
    // watermark and silently dropped — the console froze for the whole
    // resumed run.
    expect(pending).toHaveLength(first + 2);
    await act(async () => {
      pending[first + 1].onEvent({
        type: "log", level: "info", message: "resumed and visible", seq: 1,
      });
      pending[first + 1].onEvent({
        type: "usage", data: usageWithStage(320, "Resumed attempt"), seq: 2,
      });
      pending[first + 1].onEvent(
        { type: "result", data: { status: "generated" }, seq: 3 } as never,
      );
      pending[first + 1].resolve({ status: "generated" });
      await Promise.resolve();
    });
    expect(screen.getByTestId("lines").textContent).toContain(
      "resumed and visible",
    );
    expect(screen.getByTestId("usage-stages").textContent).toBe(
      "Resumed attempt",
    );
    expect(screen.getByTestId("status").textContent).toBe("done");
  } finally {
    vi.useRealTimers();
    pending.length = 0;
    getUploadJobMock.mockReset();
    getRunEventsMock.mockReset();
  }
});


test("a non-transient catch-up failure stops the poll instead of spinning forever", async () => {
  vi.useFakeTimers();
  try {
    getRunEventsMock.mockRejectedValue(
      Object.assign(new Error("authentication required"), { status: 401 }),
    );

    function AuthStopProbe() {
      const { run, state } = useRunConsole();
      return (
        <>
          <button
            onClick={() => void run(
              "Generate",
              "/generate",
              {},
              undefined,
              { module: "concepts", jobId: 11 },
            ).catch(() => undefined)}
          >
            Generate
          </button>
          <output data-testid="lines">
            {state.lines.map((line) => line.message).join(" | ")}
          </output>
          <output data-testid="status">{state.status}</output>
        </>
      );
    }
    render(
      <RunConsoleProvider>
        <AuthStopProbe />
      </RunConsoleProvider>,
    );

    const first = pending.length;
    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    await act(async () => {
      pending[first].reject(transportError("network connection lost"));
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    // The session died, not the network: the loop stops with a spoken
    // reason. Left alone this poll ran every 15s for as long as the tab
    // lived — the "something running continuously in the background".
    expect(screen.getByTestId("status").textContent).toBe("error");
    expect(screen.getByTestId("lines").textContent).toContain(
      "Catch-up stopped",
    );
    const callsAfterStop = getRunEventsMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(getRunEventsMock.mock.calls.length).toBe(callsAfterStop);
  } finally {
    vi.useRealTimers();
    pending.length = 0;
    getUploadJobMock.mockReset();
    getRunEventsMock.mockReset();
  }
});
