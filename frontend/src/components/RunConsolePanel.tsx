import { useEffect, useRef } from "react";
import { useRunConsole } from "../RunConsole";
import ApiUsageSummary from "./ApiUsageSummary";

const LEVEL_CLASS: Record<string, string> = {
  info: "log-info",
  step: "log-step",
  success: "log-success",
  warn: "log-warn",
  warning: "log-warn",
  error: "log-error",
  debug: "log-debug",
};

export default function RunConsolePanel() {
  const { state, setOpen, clear } = useRunConsole();
  const bodyRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the newest line as the run streams in.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state.lines, state.open]);

  if (!state.open) {
    return (
      <button className="console-tab" onClick={() => setOpen(true)} title="Show activity log">
        {state.active ? "● Running…" : "Console"}
      </button>
    );
  }

  const pct = Math.round(state.progress * 100);
  const statusDot =
    state.status === "running" ? "dot-running"
      : state.status === "error" ? "dot-error"
        : state.status === "done" ? "dot-done" : "dot-idle";

  return (
    <aside className="console">
      <div className="console-head">
        <span className={`status-dot ${statusDot}`} />
        <strong className="console-title">{state.title || "Activity log"}</strong>
        <div className="spacer" />
        <button className="ghost console-btn" onClick={clear} disabled={state.active}>Clear</button>
        <button className="ghost console-btn" onClick={() => setOpen(false)}>Hide</button>
      </div>

      {(state.status !== "idle") && (
        <div className="console-progress">
          <div className="progress-track">
            <div
              className={`progress-fill ${state.status === "error" ? "progress-err" : ""}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="console-progress-label">
            <span>{state.progressLabel}</span>
            <span className="mono">{pct}%</span>
          </div>
        </div>
      )}

      <ApiUsageSummary usage={state.usage} compact />

      <div className="console-body" ref={bodyRef}>
        {state.lines.length === 0 && (
          <div className="muted" style={{ padding: 8 }}>
            Run any generation, conversion or workbook action to watch live progress here.
          </div>
        )}
        {state.lines.map((l, i) => (
          <div key={i} className={`console-line ${LEVEL_CLASS[l.level] ?? "log-info"}`}>
            <span className="console-time">{fmtTime(l.ts)}</span>
            <span className="console-msg">{l.level === "step" ? `▸ ${l.message}` : l.message}</span>
          </div>
        ))}
      </div>
    </aside>
  );
}

function fmtTime(ts: number): string {
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour12: false });
  } catch {
    return "";
  }
}
