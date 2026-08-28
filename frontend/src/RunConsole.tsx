import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import {
  api as httpApi,
  isNonTransientStatus,
  streamNdjson,
  type StreamEvent,
} from "./api/client";
import type { OpenAIUsage } from "./types";

export interface RunLine {
  level: string;
  message: string;
  ts: number;
  /** The parallel track (label scope) that emitted this line, if any. */
  lane?: string;
}

export interface RunState {
  active: boolean;          // a run is in progress
  open: boolean;            // console panel expanded
  title: string;
  lines: RunLine[];
  progress: number;         // 0..1
  progressLabel: string;
  startedAt: number | null; // epoch seconds when the run began
  status: "idle" | "running" | "paused" | "done" | "error";
  usage: OpenAIUsage | null;
  usagePresentation: RunUsagePresentation | null;
}

/**
 * How to re-attach when the network drops mid-run. Generation executes in a
 * server-side worker and keeps going without the connection, so a transport
 * failure is never treated as the run failing: the console waits for the
 * network, watches the job, and either resumes the stream from the saved
 * checkpoint (a designed, replay-from-cache re-POST) or recovers the result
 * of a run that finished while the connection was down. The re-attach
 * request also wakes a stopped machine, which then resumes from checkpoint.
 */
export interface RunReattach<T = unknown> {
  module: "assessments" | "concepts";
  jobId: number;
  /** Build the resolved value when the run finished while disconnected. */
  recoverResult?: () => Promise<T>;
}

export interface RunUsagePresentation {
  cumulative?: boolean;
  resumed?: boolean;
  filename?: string;
  fileLabel?: string;
  initialUsage?: OpenAIUsage | null;
}

interface RunConsoleApi {
  state: RunState;
  setOpen: (open: boolean) => void;
  clear: () => void;
  /** POST a streaming endpoint, piping its events into the console. */
  run: <T = unknown>(
    title: string,
    path: string,
    init?: RequestInit,
    usagePresentation?: RunUsagePresentation,
    reattach?: RunReattach<T>,
  ) => Promise<T>;
  /**
   * Attach to an ALREADY-RUNNING job without starting or resuming
   * anything: tail its durable run journal into the console from the
   * beginning (the replay rebuilds the stage cards with their real
   * times and costs) and keep following while the worker runs. Makes
   * no POST, spends nothing, and never resumes a stopped run — if the
   * worker died mid-run it says so and stops. Resolves with the run's
   * result when it finishes (via recoverResult when provided), or null
   * when there is nothing to return.
   */
  watch: <T = unknown>(
    title: string,
    reattach: RunReattach<T>,
  ) => Promise<T | null>;
}

/* The console is the run's full record: keep enough lines that even a long
   multi-phase generation stays reviewable end to end. */
const MAX_LINES = 3000;

/* On a phone the docked console would cover half the viewport, so it
   starts CLOSED there and opens itself the moment a run starts — the
   log appears exactly when there is a log to watch. Desktop keeps the
   always-open panel. */
const SMALL_SCREEN =
  typeof window !== "undefined"
  && typeof window.matchMedia === "function"
  && window.matchMedia("(max-width: 960px)").matches;

/* Phone browsers freeze background tabs and kill their open streams: a
   tab switch or screen lock is the COMMON way the run stream drops, and
   it is not a network problem. The run keeps executing on the server
   and journals every event, so a drop of ANY kind is handled by tailing
   the journal silently (see withReattach) — the console holds its last
   state while away and repaints to exactly-current the moment the tab
   is visible again. */

const INITIAL: RunState = {
  active: false, open: !SMALL_SCREEN, title: "", lines: [], progress: 0,
  progressLabel: "", startedAt: null, status: "idle", usage: null,
  usagePresentation: null,
};

const RunConsoleContext = createContext<RunConsoleApi | null>(null);

/* Waits ms — but resolves EARLY the moment the tab becomes visible again,
   so the first poll after a tab switch is immediate instead of stuck
   behind a background-throttled timer. */
function visibilitySleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const finish = () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      resolve();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") finish();
    };
    const timer = window.setTimeout(finish, ms);
    document.addEventListener("visibilitychange", onVisible);
  });
}

export function RunConsoleProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<RunState>(INITIAL);
  const openRef = useRef(!SMALL_SCREEN);
  const runIdRef = useRef(0);

  const apply = useCallback((evt: StreamEvent) => {
    setState((s) => {
      if (evt.type === "heartbeat") {
        if (!s.active || s.progressLabel.includes("still working")) return s;
        const label = s.progressLabel
          ? `${s.progressLabel} (still working...)`
          : "Still working...";
        return { ...s, progressLabel: label };
      }
      const next = { ...s };
      if (evt.type === "progress") {
        next.progress = evt.value;
        if (evt.label) next.progressLabel = evt.label;
      } else if (evt.type === "step") {
        next.progressLabel = evt.label;
        next.lines = [...s.lines, { level: "step", message: evt.label, ts: evt.ts ?? Date.now() / 1000 }];
      } else if (evt.type === "log") {
        next.lines = [...s.lines, {
          level: evt.level ?? "info",
          message: evt.message,
          ts: evt.ts ?? Date.now() / 1000,
          ...(evt.lane ? { lane: evt.lane } : {}),
        }];
      } else if (evt.type === "usage") {
        next.usage = presentedUsage(s, evt.data);
      } else if (evt.type === "result") {
        const resultUsage = usageFromResult(evt.data);
        if (resultUsage) next.usage = presentedUsage(s, resultUsage);
      } else if (evt.type === "error") {
        next.lines = [...s.lines, { level: "error", message: evt.message, ts: evt.ts ?? Date.now() / 1000 }];
        if (evt.openai_usage) {
          next.usage = presentedUsage(s, evt.openai_usage);
        }
      }
      if (next.lines.length > MAX_LINES) next.lines = next.lines.slice(-MAX_LINES);
      return next;
    });
  }, []);

  const run = useCallback(<T,>(
    title: string,
    path: string,
    init: RequestInit = {},
    usagePresentation?: RunUsagePresentation,
    reattach?: RunReattach<T>,
  ): Promise<T> => {
    const runId = ++runIdRef.current;
    const {
      initialUsage = null,
      ...presentation
    } = usagePresentation ?? {};
    setState({
      active: true,
      open: true,
      title,
      lines: [],
      progress: 0,
      startedAt: Date.now() / 1000,
      // The server's ledger is cumulative across run segments (parse +
      // every attempt), stage rows included, so durable initial usage is
      // shown as-is — the first live event carries the same merged table.
      usage: initialUsage,
      usagePresentation: usagePresentation ? presentation : null,
      progressLabel: "Starting…", status: "running",
    });
    openRef.current = true;

    // The durable journal's cursor: events carry a monotonic `seq` on
    // both the live stream and the catch-up reads, so an event is
    // applied exactly once however it arrives.
    let lastSeq = 0;
    const applyOnce = (event: StreamEvent) => {
      const seq = (event as { seq?: number }).seq;
      if (typeof seq === "number") {
        if (seq <= lastSeq) return;
        lastSeq = seq;
      }
      apply(event);
    };

    const attempt = () =>
      streamNdjson<T>(path, { method: "POST", ...init }, (event) => {
        if (runIdRef.current === runId) applyOnce(event);
      });

    const note = (message: string, level = "warning") => {
      if (runIdRef.current !== runId) return;
      setState((s) => ({
        ...s,
        lines: [...s.lines, { level, message, ts: Date.now() / 1000 }],
        progressLabel: message,
      }));
    };
    const sleep = visibilitySleep;

    const withReattach = async (): Promise<T> => {
      // Bounded: a run that keeps dying for non-network reasons must surface,
      // not be silently restarted forever.
      for (let reattachesLeft = 5; ; ) {
        try {
          return await attempt();
        } catch (err) {
          // Matched by name, not class identity: the client module can be
          // mocked or re-instantiated, and a transport error must still be
          // recognised as one.
          const isTransport =
            err instanceof Error && err.name === "StreamTransportError";
          if (!reattach || !isTransport || runIdRef.current !== runId) {
            throw err;
          }
          // The stream dropped — a tab switch, a screen lock, a flaky
          // radio. The run keeps executing on the server and every event
          // it emits lands in the durable journal, so this client tails
          // that journal SILENTLY: the console keeps its last state
          // while away, repaints to exactly-current on return (sleep()
          // resolves the moment the tab is visible again), and even
          // finishes the run from the journal's terminal event. Nothing
          // is said unless the network is genuinely gone for a while, or
          // the worker itself died and a checkpoint resume is needed —
          // only the latter consumes the bounded budget.
          let delay = 250;
          let outageNoted = false;
          let lastPollOk = Date.now();
          for (;;) {
            if (runIdRef.current !== runId) throw err;
            await sleep(delay);
            let tail;
            try {
              tail = await httpApi.getRunEvents(
                reattach.module, reattach.jobId, lastSeq,
              );
            } catch (pollErr) {
              // The server answered and said no (session expired, job
              // gone): retrying every 15s forever is the "something is
              // running continuously in the background" the owner
              // reported. Stop the loop and say why.
              if (isNonTransientStatus(pollErr)) {
                note(
                  "Catch-up stopped: "
                  + String((pollErr as Error).message ?? pollErr)
                  + " — reload the page to reattach.",
                  "error",
                );
                throw pollErr;
              }
              delay = Math.min(Math.max(delay * 2, 1000), 15000);
              if (
                !outageNoted
                && document.visibilityState === "visible"
                && Date.now() - lastPollOk > 10000
              ) {
                outageNoted = true;
                note(
                  "Network connection lost — the run continues on the "
                  + "server. Waiting for the network…",
                );
              }
              continue;
            }
            lastPollOk = Date.now();
            outageNoted = false;
            delay = 2000;
            for (const event of tail.events) {
              if (runIdRef.current !== runId) throw err;
              applyOnce(event);
              if (event.type === "result") return event.data as T;
              if (event.type === "error") throw new Error(event.message);
            }
            if (tail.running) continue;
            // Journal exhausted, worker not running, no terminal event:
            // the server process itself stopped mid-run (a machine
            // restart). Older runs without a journal land here too —
            // fall back to job status before resuming from checkpoint.
            let job;
            try {
              job = await httpApi.getUploadJob(reattach.module, reattach.jobId);
            } catch (jobErr) {
              if (isNonTransientStatus(jobErr)) {
                note(
                  "Catch-up stopped: "
                  + String((jobErr as Error).message ?? jobErr)
                  + " — reload the page to reattach.",
                  "error",
                );
                throw jobErr;
              }
              continue;
            }
            if (job.generation_running) continue;
            if (job.status === "generated") {
              note("The run finished while the connection was down.", "info");
              if (reattach.recoverResult) return await reattach.recoverResult();
              throw new Error(
                "Generation completed while the connection was down; "
                + "reload the page to see the result.",
              );
            }
            if (reattachesLeft <= 0) throw err;
            reattachesLeft -= 1;
            // Re-POST the same request: the server resumes from the
            // durable checkpoint and replays finished work from cache.
            // A re-POST starts a NEW stream, and the journal restarts
            // with it (run_journal truncates and seq begins at 1 again)
            // — so the cursor resets too. Without this, every resumed
            // event arrived <= the dead run's watermark and was dropped
            // as a duplicate, the catch-up tail was filtered server-side
            // forever, and the terminal event could never finish the
            // run — the client kept polling and re-POSTing instead.
            lastSeq = 0;
            // Stage rows are cumulative across attempts now, so the
            // resumed stream's first usage event carries the same merged
            // table — nothing to strip here.
            note("Resuming the run from its saved checkpoint…", "info");
            break;
          }
        }
      }
    };

    return withReattach()
      .then((data) => {
        if (runIdRef.current === runId) {
          setState((s) => {
            if (isAwaitingDecisionResult(data)) {
              return {
                ...s,
                active: false,
                status: "paused",
                progress: decisionCheckpointProgress(data) ?? s.progress,
                progressLabel: "Paused for your decision",
              };
            }
            return {
              ...s,
              active: false,
              status: "done",
              progress: 1,
              progressLabel: "Done",
            };
          });
        }
        return data;
      })
      .catch((err) => {
        const message = String(err?.message ?? err);
        if (runIdRef.current === runId) {
          setState((s) => ({
            ...s, active: false, status: "error",
            lines: [...s.lines, { level: "error", message, ts: Date.now() / 1000 }],
          }));
        }
        throw err;
      });
  }, [apply]);

  const watch = useCallback(<T,>(
    title: string,
    reattach: RunReattach<T>,
  ): Promise<T | null> => {
    const runId = ++runIdRef.current;
    setState({
      ...INITIAL,
      active: true,
      open: true,
      title,
      progressLabel: "Attaching to the running job…",
      status: "running",
    });
    openRef.current = true;

    let lastSeq = 0;
    let startedAtSet = false;
    const applyOnce = (event: StreamEvent) => {
      const seq = (event as { seq?: number }).seq;
      if (typeof seq === "number") {
        if (seq <= lastSeq) return;
        lastSeq = seq;
      }
      if (!startedAtSet && typeof event.ts === "number") {
        // The replay carries the run's REAL timestamps: the header clock
        // and the stage cards show when things actually happened, not
        // when this viewer attached.
        startedAtSet = true;
        const startedAt = event.ts;
        setState((s) => ({ ...s, startedAt }));
      }
      apply(event);
    };
    const note = (message: string, level = "warning") => {
      if (runIdRef.current !== runId) return;
      setState((s) => ({
        ...s,
        lines: [...s.lines, { level, message, ts: Date.now() / 1000 }],
        progressLabel: message,
      }));
    };

    type Outcome =
      | { kind: "result"; data: T | null }
      | { kind: "stopped" }
      | { kind: "detached" };

    const tail = async (): Promise<Outcome> => {
      let delay = 250;
      for (;;) {
        if (runIdRef.current !== runId) return { kind: "detached" };
        let batch;
        try {
          batch = await httpApi.getRunEvents(
            reattach.module, reattach.jobId, lastSeq,
          );
        } catch (pollErr) {
          if (isNonTransientStatus(pollErr)) {
            note(
              "Watching stopped: "
              + String((pollErr as Error).message ?? pollErr),
              "error",
            );
            throw pollErr;
          }
          delay = Math.min(Math.max(delay * 2, 1000), 15000);
          await visibilitySleep(delay);
          continue;
        }
        delay = 2000;
        for (const event of batch.events) {
          if (runIdRef.current !== runId) return { kind: "detached" };
          applyOnce(event);
          if (event.type === "result") {
            return { kind: "result", data: event.data as T };
          }
          if (event.type === "error") throw new Error(event.message);
        }
        if (batch.running) {
          await visibilitySleep(delay);
          continue;
        }
        // Journal exhausted, worker not streaming, no terminal event yet.
        let job;
        try {
          job = await httpApi.getUploadJob(reattach.module, reattach.jobId);
        } catch (jobErr) {
          if (isNonTransientStatus(jobErr)) {
            note(
              "Watching stopped: "
              + String((jobErr as Error).message ?? jobErr),
              "error",
            );
            throw jobErr;
          }
          await visibilitySleep(delay);
          continue;
        }
        if (job.generation_running) {
          await visibilitySleep(delay);
          continue;
        }
        if (job.status === "generated") {
          note("The run finished.", "info");
          if (reattach.recoverResult) {
            return { kind: "result", data: await reattach.recoverResult() };
          }
          return { kind: "result", data: null };
        }
        // Watch-only NEVER resumes: a worker that stopped mid-run is
        // reported, and the resume stays an explicit human act.
        note(
          "The worker is not running. Open the saved run to resume it "
          + "from the checkpoint — watching never restarts a run.",
        );
        return { kind: "stopped" };
      }
    };

    return tail()
      .then((outcome) => {
        if (runIdRef.current === runId) {
          if (outcome.kind === "result") {
            setState((s) => ({
              ...s, active: false, status: "done",
              progress: 1, progressLabel: "Done",
            }));
          } else if (outcome.kind === "stopped") {
            setState((s) => ({ ...s, active: false, status: "paused" }));
          }
        }
        return outcome.kind === "result" ? outcome.data : null;
      })
      .catch((err) => {
        if (runIdRef.current === runId) {
          setState((s) => ({
            ...s, active: false, status: "error",
            lines: [...s.lines, {
              level: "error",
              message: String((err as Error)?.message ?? err),
              ts: Date.now() / 1000,
            }],
          }));
        }
        throw err;
      });
  }, [apply]);

  const setOpen = useCallback((open: boolean) => {
    openRef.current = open;
    setState((s) => ({ ...s, open }));
  }, []);

  const clear = useCallback(() => {
    runIdRef.current += 1;
    setState((s) => ({
      ...INITIAL, open: s.open, status: "idle",
    }));
  }, []);

  const api = useMemo<RunConsoleApi>(
    () => ({ state, setOpen, clear, run, watch }),
    [state, setOpen, clear, run, watch],
  );

  return <RunConsoleContext.Provider value={api}>{children}</RunConsoleContext.Provider>;
}

export function useRunConsole(): RunConsoleApi {
  const ctx = useContext(RunConsoleContext);
  if (!ctx) throw new Error("useRunConsole must be used within RunConsoleProvider");
  return ctx;
}

function usageFromResult(data: unknown): OpenAIUsage | null {
  if (!data || typeof data !== "object") return null;
  const usage = (data as Record<string, unknown>).openai_usage;
  if (!usage || typeof usage !== "object") return null;
  const requestCount = (usage as Record<string, unknown>).request_count;
  const totalTokens = (usage as Record<string, unknown>).total_tokens;
  if (typeof requestCount !== "number" || typeof totalTokens !== "number") return null;
  return usage as OpenAIUsage;
}

function presentedUsage(
  state: RunState,
  incoming: OpenAIUsage,
): OpenAIUsage {
  if (
    state.usagePresentation?.cumulative
    && state.usage
    && numericUsage(incoming.total_tokens)
      < numericUsage(state.usage.total_tokens)
  ) {
    // A file-scoped cumulative display must never visually reset to a smaller
    // fresh-attempt subtotal while a checkpoint retry is starting.
    return state.usage;
  }
  if (!incoming.stages && state.usage?.stages) {
    // Every summary now carries the cumulative stage table; this guard
    // only protects against a legacy event without one, keeping the
    // stage cards' cost chips through "Done".
    return { ...incoming, stages: state.usage.stages };
  }
  return incoming;
}

function numericUsage(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function isAwaitingDecisionResult(data: unknown): boolean {
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  const result = data as Record<string, unknown>;
  return result.status === "awaiting_decision"
    && Boolean(result.pending_decision);
}

function decisionCheckpointProgress(data: unknown): number | null {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const result = data as Record<string, unknown>;
  const pending = result.pending_decision;
  const raw = pending && typeof pending === "object" && !Array.isArray(pending)
    ? (pending as Record<string, unknown>).checkpoint_progress
    : result.checkpoint_progress;
  return typeof raw === "number" && Number.isFinite(raw)
    ? Math.max(0, Math.min(1, raw))
    : null;
}
