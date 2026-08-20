import { useEffect, useMemo, useRef, useState } from "react";
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

type Filter = "all" | "steps" | "issues";

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All" },
  { key: "steps", label: "Steps" },
  { key: "issues", label: "Issues" },
];

function matches(filter: Filter, level: string): boolean {
  if (filter === "all") return true;
  if (filter === "steps") return level === "step" || level === "success" || level === "error";
  return level === "warn" || level === "warning" || level === "error";
}

/* The run console: the app's full activity record. Every stream event —
   steps, logs, warnings, errors — lands here with a timestamp, filterable
   and copyable. It follows the newest line unless the reader scrolls back
   up, in which case a jump pill offers the way back down. */
export default function RunConsolePanel() {
  const { state, setOpen, clear } = useRunConsole();
  const bodyRef = useRef<HTMLDivElement>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [follow, setFollow] = useState(true);
  const [copied, setCopied] = useState(false);
  const [, forceTick] = useState(0);

  // On phones the open console is a fixed bottom sheet; this class lets
  // the page reserve matching bottom space so content and focused inputs
  // can always scroll clear of it (styles.css, ≤960px block).
  useEffect(() => {
    document.body.classList.toggle("console-open", state.open);
    return () => document.body.classList.remove("console-open");
  }, [state.open]);

  // A once-a-second tick keeps the elapsed clock honest while a run is live.
  useEffect(() => {
    if (!state.active || !state.startedAt) return;
    const id = window.setInterval(() => forceTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [state.active, state.startedAt]);

  // Follow the stream: stick to the newest line unless the reader scrolled up.
  useEffect(() => {
    const el = bodyRef.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [state.lines, state.open, follow, filter]);

  const visible = useMemo(
    () => state.lines.filter((l) => matches(filter, l.level)),
    [state.lines, filter],
  );

  if (!state.open) {
    return (
      <button className="console-tab" onClick={() => setOpen(true)} title="Show activity log">
        {state.active
          ? "● Running…"
          : state.status === "paused"
            ? "● Paused"
            : "Console"}
      </button>
    );
  }

  const pct = Math.round(state.progress * 100);
  const statusDot =
    state.status === "running" ? "dot-running"
      : state.status === "paused" ? "dot-paused"
      : state.status === "error" ? "dot-error"
        : state.status === "done" ? "dot-done" : "dot-idle";
  const fillClass =
    state.status === "error" ? "progress-err"
      : state.status === "paused" ? "progress-paused"
        : state.status === "done" ? "progress-done"
          : state.active ? "progress-active" : "";

  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atBottom !== follow) setFollow(atBottom);
  };

  const copyLog = async () => {
    const text = state.lines
      .map((l) => `${fmtTime(l.ts)}  [${l.level.padEnd(7)}] ${l.message}`)
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (permissions/insecure context) — leave quietly */
    }
  };

  return (
    <aside className="console">
      <div className="console-head">
        <span className={`status-dot ${statusDot}`} />
        <strong className="console-title">{state.title || "Activity log"}</strong>
        <div className="spacer" />
        <button className="ghost console-btn" onClick={copyLog} disabled={state.lines.length === 0}>
          {copied ? "Copied" : "Copy"}
        </button>
        <button className="ghost console-btn" onClick={clear} disabled={state.active}>Clear</button>
        <button className="ghost console-btn" onClick={() => setOpen(false)}>Hide</button>
      </div>

      {(state.startedAt !== null || state.lines.length > 0) && (
        <div className="console-meta">
          {state.startedAt !== null && (
            <span title="Elapsed since the run started">⏱ {fmtElapsed(state.startedAt, state.active)}</span>
          )}
          <span>{state.lines.length} event{state.lines.length === 1 ? "" : "s"}</span>
        </div>
      )}

      {(state.status !== "idle") && (
        <div className="console-progress">
          <div className="progress-track">
            <div className={`progress-fill ${fillClass}`} style={{ width: `${pct}%` }} />
          </div>
          <div className="console-progress-label">
            <span>{state.progressLabel}</span>
            <span className="mono">{pct}%</span>
          </div>
        </div>
      )}

      <ApiUsageSummary
        usage={state.usage}
        compact
        cumulative={state.usagePresentation?.cumulative}
        resumed={state.usagePresentation?.resumed}
        filename={state.usagePresentation?.filename}
        fileLabel={state.usagePresentation?.fileLabel}
      />

      {state.lines.length > 0 && (
        <div className="console-filters" role="group" aria-label="Filter log lines">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              className={`console-filter${filter === f.key ? " console-filter-on" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}

      <div className="console-body-wrap">
        <div className="console-body" ref={bodyRef} onScroll={onScroll}>
          {state.lines.length === 0 && (
            <div className="console-empty">
              Run any generation, conversion or workbook action to watch live progress here.
            </div>
          )}
          {state.lines.length > 0 && visible.length === 0 && (
            <div className="console-empty">No lines match this filter yet.</div>
          )}
          {visible.map((l, i) => (
            <div key={i} className={`console-line ${LEVEL_CLASS[l.level] ?? "log-info"}`}>
              <span className="console-time">{fmtTime(l.ts)}</span>
              <span className="console-msg">{l.level === "step" ? `▸ ${l.message}` : l.message}</span>
            </div>
          ))}
        </div>
        {!follow && state.lines.length > 0 && (
          <button className="console-jump" onClick={() => setFollow(true)}>
            ↓ Follow latest
          </button>
        )}
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

function fmtElapsed(startedAt: number, active: boolean): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  const clock = m >= 60
    ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`
    : `${m}:${String(s).padStart(2, "0")}`;
  return active ? clock : `${clock} total`;
}
