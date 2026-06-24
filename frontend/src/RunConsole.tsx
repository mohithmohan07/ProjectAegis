import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { streamNdjson, type StreamEvent } from "./api/client";

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
  progressLabel: "", status: "idle",
};

const RunConsoleContext = createContext<RunConsoleApi | null>(null);

export function RunConsoleProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<RunState>(INITIAL);
  const openRef = useRef(true);

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
      } else if (evt.type === "error") {
        next.lines = [...s.lines, { level: "error", message: evt.message, ts: evt.ts ?? Date.now() / 1000 }];
      }
      if (next.lines.length > MAX_LINES) next.lines = next.lines.slice(-MAX_LINES);
      return next;
    });
  }, []);

  const run = useCallback(<T,>(title: string, path: string, init: RequestInit = {}): Promise<T> => {
    setState({
      active: true, open: true, title, lines: [], progress: 0,
      progressLabel: "Starting…", status: "running",
    });
    openRef.current = true;
    return streamNdjson<T>(path, { method: "POST", ...init }, apply)
      .then((data) => {
        setState((s) => ({ ...s, active: false, status: "done", progress: 1, progressLabel: "Done" }));
        return data;
      })
      .catch((err) => {
        const message = String(err?.message ?? err);
        setState((s) => ({
          ...s, active: false, status: "error",
          lines: [...s.lines, { level: "error", message, ts: Date.now() / 1000 }],
        }));
        throw err;
      });
  }, [apply]);

  const setOpen = useCallback((open: boolean) => {
    openRef.current = open;
    setState((s) => ({ ...s, open }));
  }, []);

  const clear = useCallback(() => setState((s) => ({
    ...INITIAL, open: s.open, status: "idle",
  })), []);

  const api = useMemo<RunConsoleApi>(() => ({ state, setOpen, clear, run }),
    [state, setOpen, clear, run]);

  return <RunConsoleContext.Provider value={api}>{children}</RunConsoleContext.Provider>;
}

export function useRunConsole(): RunConsoleApi {
  const ctx = useContext(RunConsoleContext);
  if (!ctx) throw new Error("useRunConsole must be used within RunConsoleProvider");
  return ctx;
}
