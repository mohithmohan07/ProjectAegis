import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { streamNdjson, type StreamEvent } from "./api/client";
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
  status: "idle" | "running" | "done" | "error";
  usage: OpenAIUsage | null;
}

interface RunConsoleApi {
  state: RunState;
  setOpen: (open: boolean) => void;
  clear: () => void;
  /** POST a streaming endpoint, piping its events into the console. */
  run: <T = unknown>(title: string, path: string, init?: RequestInit) => Promise<T>;
}

const MAX_LINES = 800;
const INITIAL: RunState = {
  active: false, open: true, title: "", lines: [], progress: 0,
  progressLabel: "", status: "idle", usage: null,
};

const RunConsoleContext = createContext<RunConsoleApi | null>(null);

export function RunConsoleProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<RunState>(INITIAL);
  const openRef = useRef(true);
  const runIdRef = useRef(0);

  const apply = useCallback((evt: StreamEvent) => {
    setState((s) => {
      if (evt.type === "heartbeat") return s;
      const next = { ...s };
      if (evt.type === "progress") {
        next.progress = evt.value;
        if (evt.label) next.progressLabel = evt.label;
      } else if (evt.type === "step") {
        next.progressLabel = evt.label;
        next.lines = [...s.lines, { level: "step", message: evt.label, ts: evt.ts ?? Date.now() / 1000 }];
      } else if (evt.type === "log") {
        next.lines = [...s.lines, { level: evt.level ?? "info", message: evt.message, ts: evt.ts ?? Date.now() / 1000 }];
      } else if (evt.type === "usage") {
        next.usage = evt.data;
      } else if (evt.type === "result") {
        next.usage = usageFromResult(evt.data) ?? next.usage;
      } else if (evt.type === "error") {
        next.lines = [...s.lines, { level: "error", message: evt.message, ts: evt.ts ?? Date.now() / 1000 }];
        next.usage = evt.openai_usage ?? next.usage;
      }
      if (next.lines.length > MAX_LINES) next.lines = next.lines.slice(-MAX_LINES);
      return next;
    });
  }, []);

  const run = useCallback(<T,>(title: string, path: string, init: RequestInit = {}): Promise<T> => {
    const runId = ++runIdRef.current;
    setState({
      active: true, open: true, title, lines: [], progress: 0, usage: null,
      progressLabel: "Starting…", status: "running",
    });
    openRef.current = true;
    return streamNdjson<T>(path, { method: "POST", ...init }, (event) => {
      if (runIdRef.current === runId) apply(event);
    })
      .then((data) => {
        if (runIdRef.current === runId) {
          setState((s) => ({ ...s, active: false, status: "done", progress: 1, progressLabel: "Done" }));
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
