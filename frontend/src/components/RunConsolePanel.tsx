import { useEffect, useMemo, useRef, useState } from "react";
import { useRunConsole, type RunLine } from "../RunConsole";
import {
  fmtStageElapsed,
  groupStages,
  latestStageOccurrenceIndexes,
  stageCost,
  type StageGroup,
} from "../lib/runStages";
import type { StageUsageRow } from "../types";
import ApiUsageSummary, {
  formatEstimatedCost,
  formatTokenCount,
} from "./ApiUsageSummary";

/* On a phone the console is a bottom sheet: the log needs the room, so
   the usage block starts FOLDED there (one line, tap to open) and the
   sheet can expand to full screen. Desktop keeps everything open. */
const SMALL_SCREEN =
  typeof window !== "undefined"
  && typeof window.matchMedia === "function"
  && window.matchMedia("(max-width: 960px)").matches;

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
type View = "stages" | "raw";

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All" },
  { key: "steps", label: "Steps" },
  { key: "issues", label: "Issues" },
];

/* A stable small palette for lane rails: the same lane keeps its colour
   for the whole run (hashed by name), and the colours read in both
   themes because only the RAIL is tinted, never the text. */
const LANE_COLORS = [
  "#4f8ef7", "#2fb344", "#e6a23c", "#b76ef0", "#2bb8c4", "#ef6292",
];

function laneColor(lane: string): string {
  let hash = 0;
  for (let i = 0; i < lane.length; i += 1) {
    hash = (hash * 31 + lane.charCodeAt(i)) | 0;
  }
  return LANE_COLORS[Math.abs(hash) % LANE_COLORS.length];
}

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
  const [view, setView] = useState<View>("stages");
  const [expanded, setExpanded] = useState(false);
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

  const stages = useMemo(() => groupStages(state.lines), [state.lines]);
  const newestStageOccurrences = useMemo(
    () => latestStageOccurrenceIndexes(stages),
    [stages],
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
  const usageState = state.active
    ? "live — still accumulating"
    : state.status === "done"
      ? "final for this run"
      : "recorded so far";

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
    <aside className={`console${expanded ? " console-expanded" : ""}`}>
      <div className="console-head">
        <span className={`status-dot ${statusDot}`} />
        <strong className="console-title">{state.title || "Activity log"}</strong>
        <div className="spacer" />
        <button className="ghost console-btn" onClick={copyLog} disabled={state.lines.length === 0}>
          {copied ? "Copied" : "Copy"}
        </button>
        <button className="ghost console-btn" onClick={clear} disabled={state.active}>Clear</button>
        <button
          className="ghost console-btn console-expand-btn"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Shrink" : "Expand"}
        </button>
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

      {state.usage && (
        <details className="console-usage-fold" open={!SMALL_SCREEN}>
          <summary>
            Model usage ({usageState})
            {" · "}
            {formatTokenCount(state.usage.total_tokens)} tokens
            {state.usage.estimated_cost_usd != null && (
              <> · {formatEstimatedCost(state.usage.estimated_cost_usd)}</>
            )}
          </summary>
          <ApiUsageSummary
            usage={state.usage}
            compact
            cumulative={state.usagePresentation?.cumulative}
            resumed={state.usagePresentation?.resumed}
            filename={state.usagePresentation?.filename}
            fileLabel={state.usagePresentation?.fileLabel}
          />
        </details>
      )}

      {state.lines.length > 0 && (
        <div className="console-filters" role="group" aria-label="Log view">
          <button
            className={`console-filter${view === "stages" ? " console-filter-on" : ""}`}
            onClick={() => setView("stages")}
          >
            Stages
          </button>
          <button
            className={`console-filter${view === "raw" ? " console-filter-on" : ""}`}
            onClick={() => setView("raw")}
          >
            Raw
          </button>
          {view === "raw" && (
            <span className="console-filter-split" role="group" aria-label="Filter log lines">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  className={`console-filter${filter === f.key ? " console-filter-on" : ""}`}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </span>
          )}
        </div>
      )}

      <div className="console-body-wrap">
        <div className="console-body" ref={bodyRef} onScroll={onScroll}>
          {state.lines.length === 0 && (
            <div className="console-empty">
              Run any generation, conversion or workbook action to watch live progress here.
            </div>
          )}
          {view === "stages" && stages.map((group, index) => (
            <StageCard
              key={`${group.title}-${index}`}
              group={group}
              running={state.active && index === stages.length - 1}
              usageRows={
                newestStageOccurrences.has(index)
                  ? state.usage?.stages
                  : undefined
              }
            />
          ))}
          {view === "raw" && state.lines.length > 0 && visible.length === 0 && (
            <div className="console-empty">No lines match this filter yet.</div>
          )}
          {view === "raw" && visible.map((l, i) => (
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

/* One stage card: status, elapsed, cost/token chips, and the stage's log
   lines — grouped by lane when parallel tracks spoke — inside a native
   <details> so finished stages fold away and the running one stays open.
   The card is block-level and the chips wrap, so the same markup reads
   correctly in the phone bottom sheet. */
function StageCard({
  group,
  running,
  usageRows,
}: {
  group: StageGroup;
  running: boolean;
  usageRows: StageUsageRow[] | undefined;
}) {
  const cost = stageCost(usageRows, group.title);
  const elapsedSeconds = running
    ? Date.now() / 1000 - group.startTs
    : group.endTs - group.startTs;
  const icon = running
    ? "⟳"
    : group.hasError
      ? "✕"
      : group.hasWarning
        ? "⚠"
        : "✓";
  const iconClass = running
    ? "stage-icon-running"
    : group.hasError
      ? "stage-icon-error"
      : group.hasWarning
        ? "stage-icon-warn"
        : "stage-icon-done";
  const laneless = group.lines.filter((line) => !line.lane);
  return (
    <details className="stage-card" open={running || group.hasError}>
      <summary className="stage-head">
        <span className={`stage-icon ${iconClass}`} aria-hidden>{icon}</span>
        <span className="stage-title">{group.title}</span>
        <span className="stage-chips">
          <span className="stage-chip" title="Time spent in this stage">
            ⏱ {fmtStageElapsed(elapsedSeconds)}
          </span>
          {cost && cost.totalTokens > 0 && (
            <span className="stage-chip" title="Tokens used by this stage">
              {formatTokenCount(cost.totalTokens)} tok
            </span>
          )}
          {cost && cost.cost != null && (
            <span className="stage-chip" title="Estimated cost of this stage">
              {formatEstimatedCost(cost.cost)}
            </span>
          )}
          {cost && cost.cachedInputTokens > 0 && (
            <span className="stage-chip" title="Input tokens served from cache">
              ↺ {formatTokenCount(cost.cachedInputTokens)} cached
            </span>
          )}
          {cost && cost.cacheWriteTokens > 0 && (
            <span className="stage-chip" title="Input tokens written to cache">
              ⇧ {formatTokenCount(cost.cacheWriteTokens)} cache write
            </span>
          )}
          {group.lanes.length > 1 && (
            <span className="stage-chip" title="Parallel tracks in this stage">
              ⫘ {group.lanes.length} tracks
            </span>
          )}
        </span>
      </summary>
      <div className="stage-body">
        {laneless.map((line, i) => <StageLine key={`m${i}`} line={line} />)}
        {group.lanes.map((lane) => (
          <div
            key={lane}
            className="stage-lane"
            style={{ borderLeftColor: laneColor(lane) }}
          >
            <div className="stage-lane-name" style={{ color: laneColor(lane) }}>
              {lane}
              {(() => {
                const row = cost?.lanes.find((r) => r.lane === lane);
                if (!row) return null;
                return (
                  <span className="stage-lane-cost">
                    {" · "}{formatTokenCount(row.total_tokens)} tok
                    {row.estimated_cost_usd != null && (
                      <> · {formatEstimatedCost(row.estimated_cost_usd)}</>
                    )}
                    {(row.cached_input_tokens ?? 0) > 0 && (
                      <> · {formatTokenCount(row.cached_input_tokens ?? 0)} cached</>
                    )}
                    {(row.cache_write_tokens ?? 0) > 0 && (
                      <> · {formatTokenCount(row.cache_write_tokens ?? 0)} cache write</>
                    )}
                  </span>
                );
              })()}
            </div>
            {group.lines
              .filter((line) => line.lane === lane)
              .map((line, i) => <StageLine key={`${lane}${i}`} line={line} stripLane />)}
          </div>
        ))}
      </div>
    </details>
  );
}

function StageLine({ line, stripLane }: { line: RunLine; stripLane?: boolean }) {
  const message = stripLane && line.lane
    ? line.message.replace(`[${line.lane}] `, "")
    : line.message;
  return (
    <div className={`console-line ${LEVEL_CLASS[line.level] ?? "log-info"}`}>
      <span className="console-time">{fmtTime(line.ts)}</span>
      <span className="console-msg">{message}</span>
    </div>
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
