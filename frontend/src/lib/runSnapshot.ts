import type { RunState } from "../RunConsole";

/** Export only the state already held by this console; never start server work. */
export function consoleSnapshot(state: RunState, capturedAt = new Date()) {
  return {
    format: "aegis-console-snapshot-v1",
    captured_at: capturedAt.toISOString(),
    scope: "Current console snapshot: contains only the bounded log retained in this browser and its latest received state and usage. It is not the full server diagnostics bundle and may lag the running job.",
    run: {
      title: state.title,
      active: state.active,
      status: state.status,
      progress: state.progress,
      progress_label: state.progressLabel,
      started_at_epoch_seconds: state.startedAt,
      source_filename: state.usagePresentation?.filename ?? null,
      cumulative_usage: state.usagePresentation?.cumulative ?? false,
      resumed: state.usagePresentation?.resumed ?? false,
    },
    usage: state.usage,
    log: {
      timestamp_unit: "epoch_seconds",
      retained_event_count: state.lines.length,
      first_retained_event_ts: state.lines[0]?.ts ?? null,
      last_retained_event_ts: state.lines[state.lines.length - 1]?.ts ?? null,
      events: state.lines,
    },
  };
}

export function downloadConsoleSnapshot(state: RunState) {
  const snapshot = consoleSnapshot(state);
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `aegis-console-snapshot-${snapshot.captured_at.replace(/[:.]/g, "-")}.json`;
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
