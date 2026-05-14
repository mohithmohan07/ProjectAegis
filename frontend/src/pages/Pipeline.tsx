import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";
import type { PipelineRun, StageDescriptor } from "../types";

function StageRow({ stage, onRun, run, busy }: {
  stage: StageDescriptor;
  onRun: (mode: string) => void;
  run: PipelineRun | undefined;
  busy: boolean;
}) {
  return (
    <div className="stage-row">
      <div className="stage-num">{stage.order}</div>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 600 }}>{stage.title}</div>
        <div className="muted" style={{ fontSize: 12 }}>{stage.description}</div>
        <div className="muted mono" style={{ marginTop: 4 }}>
          in: {stage.inputs.join(", ") || "—"} → out: {stage.outputs.join(", ")}
        </div>
        {stage.requires_keys.length > 0 && (
          <div style={{ marginTop: 4 }}>
            <span className={`badge ${stage.available ? "green" : "yellow"}`}>
              {stage.available ? "live ready" : `needs ${stage.requires_keys.join(", ")}`}
            </span>
          </div>
        )}
        {run && (
          <div style={{ marginTop: 6 }}>
            <span className={`badge ${run.status === "succeeded" ? "green" : run.status === "failed" ? "red" : "accent"}`}>
              {run.status}
            </span>{" "}
            <span className="muted mono">{run.detail}</span>
            {run.status === "succeeded" && (
              <pre className="mono" style={{ marginTop: 6, fontSize: 11, color: "var(--muted)" }}>
                {JSON.stringify(run.outputs, null, 2)}
              </pre>
            )}
            {run.status === "failed" && <div className="error-box" style={{ marginTop: 6 }}>{run.error}</div>}
          </div>
        )}
      </div>
      <div className="row">
        <button className="ghost" disabled={busy} onClick={() => onRun("dry")}>
          Run (dry)
        </button>
        <button disabled={busy || !stage.available} onClick={() => onRun("live")}>
          Run (live)
        </button>
      </div>
    </div>
  );
}

export default function Pipeline() {
  const stages = useAsync(() => api.stages(), []);
  const [runs, setRuns] = useState<Record<string, PipelineRun>>({});
  const [busy, setBusy] = useState<string | null>(null);

  async function run(key: string, mode: string) {
    setBusy(key);
    try {
      const result = await api.runStage(key, mode);
      setRuns((prev) => ({ ...prev, [key]: result }));
    } catch (e) {
      alert(String(e));
    } finally {
      setBusy(null);
    }
  }

  if (stages.error) return <div className="error-box">{stages.error}</div>;
  if (!stages.data) return <div className="empty">Loading…</div>;

  return (
    <>
      <h1>Pipeline</h1>
      <div className="subtitle">
        Run each stage in <strong>dry</strong> mode (dummy artifacts, no API keys) or{" "}
        <strong>live</strong> mode (delegates to the vendored scripts; needs Mathpix / OpenAI keys).
      </div>
      <div className="card">
        {stages.data.map((s) => (
          <StageRow
            key={s.key}
            stage={s}
            run={runs[s.key]}
            busy={busy === s.key}
            onRun={(mode) => run(s.key, mode)}
          />
        ))}
      </div>
    </>
  );
}
