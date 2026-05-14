import { api } from "../api/client";
import { useAsync } from "../hooks";

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="card stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export default function Dashboard() {
  const stats = useAsync(() => api.stats(), []);
  const runs = useAsync(() => api.runs(), []);

  if (stats.error) return <div className="error-box">{stats.error}</div>;
  if (!stats.data) return <div className="empty">Loading…</div>;

  const s = stats.data;
  return (
    <>
      <h1>Dashboard</h1>
      <div className="subtitle">
        Aegis turns curriculum maps and question sources into tagged, bulk-import-ready learning assets.
      </div>

      <div className="grid cols-4">
        <StatCard label="Concepts" value={s.concepts} />
        <StatCard label="Pre-Learning Concepts" value={s.pre_learning_concepts} />
        <StatCard label="Questions" value={s.questions} />
        <StatCard label="Pipeline Runs" value={s.runs} />
      </div>

      <div className="section-title">Questions by sheet</div>
      <div className="grid cols-3">
        {Object.entries(s.questions_by_sheet).map(([k, v]) => (
          <StatCard key={k} label={k} value={v} />
        ))}
      </div>

      <div className="section-title">Recent pipeline runs</div>
      <div className="card">
        {runs.data && runs.data.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Stage</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.slice(0, 8).map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{r.stage}</td>
                  <td>{r.mode}</td>
                  <td>
                    <span className={`badge ${r.status === "succeeded" ? "green" : r.status === "failed" ? "red" : ""}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="muted">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">No runs yet — head to the Pipeline tab.</div>
        )}
      </div>
    </>
  );
}
