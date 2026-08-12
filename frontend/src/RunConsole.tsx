import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { api as httpApi, streamNdjson, type StreamEvent } from "./api/client";
import type { OpenAIUsage } from "./types";

export interface RunLine {
  level: string;
  message: string;
  ts: number;
}

export interface RunState {
  active: boolean;          // a run is in progress
  open: boolean;            // console panel expanded
  title: string;
  lines: RunLine[];
  progress: number;         // 0..1
  progressLabel: string;
  etaLabel: string;         // e.g. "~7 min left" (empty when unknown)
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
}

const MAX_LINES = 800;
const INITIAL: RunState = {
  active: false, open: true, title: "", lines: [], progress: 0,
  progressLabel: "", etaLabel: "", status: "idle", usage: null,
  usagePresentation: null,
};

const RunConsoleContext = createContext<RunConsoleApi | null>(null);

export function RunConsoleProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<RunState>(INITIAL);
  const openRef = useRef(true);
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
        next.etaLabel = evt.eta_label ?? next.etaLabel;
      } else if (evt.type === "step") {
        next.progressLabel = evt.label;
        next.lines = [...s.lines, { level: "step", message: evt.label, ts: evt.ts ?? Date.now() / 1000 }];
      } else if (evt.type === "log") {
        next.lines = [...s.lines, { level: evt.level ?? "info", message: evt.message, ts: evt.ts ?? Date.now() / 1000 }];
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
      etaLabel: "",
      usage: initialUsage,
      usagePresentation: usagePresentation ? presentation : null,
      progressLabel: "Starting…", status: "running",
    });
    openRef.current = true;

    const attempt = () =>
      streamNdjson<T>(path, { method: "POST", ...init }, (event) => {
        if (runIdRef.current === runId) apply(event);
      });

    const note = (message: string, level = "warning") => {
      if (runIdRef.current !== runId) return;
      setState((s) => ({
        ...s,
        lines: [...s.lines, { level, message, ts: Date.now() / 1000 }],
        progressLabel: message,
      }));
    };
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => window.setTimeout(resolve, ms));

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
          if (
            !reattach
            || !isTransport
            || runIdRef.current !== runId
            || reattachesLeft <= 0
          ) {
            throw err;
          }
          reattachesLeft -= 1;
          note(
            "Network connection lost — the run continues on the server. "
            + "Waiting for the network and re-attaching…",
          );
          // Wait out the outage, then watch the job until the worker is done.
          // Every successful poll is also traffic that keeps (or wakes) the
          // server machine.
          let delay = 3000;
          let lastHeartbeat = Date.now();
          for (;;) {
            if (runIdRef.current !== runId) throw err;
            await sleep(delay);
            let job;
            try {
              job = await httpApi.getUploadJob(reattach.module, reattach.jobId);
            } catch {
              delay = Math.min(delay * 2, 15000);
              continue;
            }
            delay = 5000;
            if (job.generation_running) {
              if (Date.now() - lastHeartbeat > 30000) {
                lastHeartbeat = Date.now();
                note("Re-attached to the server: the run is still active.", "info");
              }
              continue;
            }
            if (job.status === "generated") {
              note("The run finished while the connection was down.", "info");
              if (reattach.recoverResult) return await reattach.recoverResult();
              throw new Error(
                "Generation completed while the connection was down; "
                + "reload the page to see the result.",
              );
            }
            // The worker stopped mid-run with a durable checkpoint. Re-POST
            // the same request: the server resumes from that checkpoint and
            // replays finished work from cache, then streams the real result.
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

  const api = useMemo<RunConsoleApi>(() => ({ state, setOpen, clear, run }),
    [state, setOpen, clear, run]);

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
