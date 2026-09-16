import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, isNonTransientStatus } from "../api/client";
import type { DashboardRun, RunDashboard } from "../types";
import { badgeClass, stateTone } from "../lib/chapterBatchState";

export function money(amount: number | null | undefined, currency = "USD"): string {
  return amount == null || !Number.isFinite(amount) ? "Unpriced" : new Intl.NumberFormat(undefined, {
    style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 4,
  }).format(amount);
}

function Stat({ label, value, note, tone = "" }: { label: string; value: string | number; note?: string; tone?: string }) {
  return <div className={`card stat dashboard-stat ${tone}`}><div className="label">{label}</div><div className="value">{value}</div>{note && <div className="hint">{note}</div>}</div>;
}

function RunRow({ row }: { row: DashboardRun }) {
  return <tr>
    <td><Link to={row.historical_source || !row.chapter_id ? `/build-concepts?job=${row.job_id}` : `/chapters?q=${encodeURIComponent(row.chapter_code)}&catalogue=all`}><strong>{row.chapter_title || row.filename}</strong></Link>
      <div className="hint">Job {row.job_id}{row.grade ? ` · Grade ${row.grade}` : ""}{row.subject ? ` · ${row.subject}` : ""}</div>
      {row.historical_source && <span className="badge">Previous source</span>}
      {row.catalogue_active === false && <span className="badge">Previous catalogue</span>}
    </td>
    <td><span className={badgeClass(stateTone(row.state))}>{row.state_label}</span>{row.stage && <div className="hint">{row.stage}</div>}{row.error && <details className="mt-4"><summary>Run details</summary><div className="hint">{row.error}</div></details>}</td>
    <td>{row.initiator_email || <span className="muted">Not recorded</span>}
      {row.notification && <div className="hint">Email: {row.notification.state}{row.notification.error ? ` · ${row.notification.error}` : ""}</div>}
    </td>
    <td className="dashboard-money"><strong>{money(row.known_cost_usd)}</strong>{!row.cost_complete && <span className="badge yellow">Partial</span>}
      {row.known_cost_inr !== null && <div className="hint">{money(row.known_cost_inr, "INR")}</div>}
      {row.pending_requests > 0 && <div className="hint">{row.pending_requests} requests pending</div>}
    </td>
    <td><div>Batch {money(row.batch_cost_usd)}</div><div className="hint">Standard {money(row.synchronous_cost_usd)}</div>
      {row.unclassified_cost_usd > 0 && <div className="hint">Unclassified {money(row.unclassified_cost_usd)}</div>}
      {row.reused_responses > 0 && <div className="hint">{row.reused_responses} reused · no new charge</div>}
      {Boolean(row.recovered_batch_receipts) && <div className="hint">{row.recovered_batch_receipts} provider receipts recovered after interruption</div>}
    </td>
  </tr>;
}

export default function Dashboard() {
  const [data, setData] = useState<RunDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [state, setState] = useState("");
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const initialStates = useRef<RunDashboard["states"]>([]);
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setPage(1); }, 300); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    let live = true;
    let pending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setLoading(true);
    const tick = async () => {
      if (!live || pending || document.hidden) { if (live) setLoading(false); return; }
      if (timer) clearTimeout(timer);
      pending = true;
      let stop = false;
      try {
        const next = await api.runDashboard({ q: query, state, page });
        if (live) { setData(next); setError(null); if (!state) initialStates.current = next.states; }
      } catch (e) { if (live) setError(String(e)); stop = isNonTransientStatus(e); }
      finally { pending = false; if (live) setLoading(false); }
      if (live && !stop) timer = setTimeout(() => void tick(), 15000);
    };
    const visible = () => { if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", visible);
    void tick();
    return () => { live = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [query, state, page, refresh]);
  const summary = data?.summary;
  return <>
    <div className="row"><div><h1>Run &amp; cost dashboard</h1><div className="subtitle">Chapter progress and cumulative API spend, including retries and previous source files.</div></div><div className="spacer" /><Link className="button-link" to="/chapters">Stage &amp; batch chapters</Link></div>
    <div className="card dashboard-toolbar">
      <div className="field"><label className="field-label" htmlFor="dashboard-search">Find a chapter or person</label><input id="dashboard-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Chapter, file, code or email" /></div>
      <div className="field"><label className="field-label" htmlFor="dashboard-state">Stage / status</label><select id="dashboard-state" value={state} onChange={(e) => { setState(e.target.value); setPage(1); }}><option value="">All stages</option>{initialStates.current.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}</select></div>
      <button className="ghost" onClick={() => setRefresh((value) => value + 1)} disabled={loading}>Refresh</button>
      {data && <span className="hint" role="status">Updated {new Date(data.server_time).toLocaleTimeString()}</span>}
    </div>
    {error && <div className="error-box mt-12" role="alert">Could not refresh the dashboard: {error}. {data ? "Showing the last successful update." : "Try Refresh."}</div>}
    {!data && loading && <div className="empty">Loading run history…</div>}
    {data && summary && <>
      <div className="grid cols-4 mt-16">
        <Stat label="Chapter runs started" value={summary.runs_started} note={`${summary.unique_chapters_started} assigned chapters · ${summary.uploaded_runs} sources staged`} />
        <Stat label="Running / queued" value={`${summary.running} / ${summary.queued}`} note={`${summary.recovering} recovering from saved progress`} />
        <Stat label="Master files complete" value={summary.masters_complete} note={`${summary.concepts_complete} Concepts complete · ${summary.published} published`} />
        <Stat label="Known API spend" value={money(summary.known_cost_usd)} note={summary.cost_complete ? "Recorded usage priced" : `${summary.incomplete_runs ?? 0} runs have incomplete costing`} tone="dashboard-accent" />
      </div>
      <div className="card mt-16 dashboard-cost-strip">
        <div><span className="field-label">Batch API</span><strong>{money(summary.batch_cost_usd)}</strong><div className="hint">{summary.batch_requests} priced receipts</div></div>
        <div><span className="field-label">Standard API</span><strong>{money(summary.synchronous_cost_usd)}</strong><div className="hint">{summary.synchronous_requests} priced receipts</div></div>
        <div><span className="field-label">Historical / unclassified</span><strong>{money(summary.unclassified_cost_usd)}</strong><div className="hint">Delivery mode was not recorded</div></div>
        <div><span className="field-label">Reused responses</span><strong>{summary.reused_responses}</strong><div className="hint">No additional model charge</div></div>
        <div><span className="field-label">Awaiting usage</span><strong>{summary.pending_requests} pending</strong><div className="hint">{summary.unresolved_requests} unresolved</div></div>
      </div>
      {Boolean(summary.unreconciled_batch_receipts) && <div className="error-box mt-8" role="status">{summary.unreconciled_batch_receipts} provider receipts ({money(summary.unreconciled_batch_cost_usd)}) may overlap historical job records and are excluded from the total until reconciled.</div>}
      <div className="hint mt-8">{data.cost_scope} {summary.known_cost_inr !== null && `Recorded INR equivalent: ${money(summary.known_cost_inr, "INR")}.`}</div>
      <div className="grid cols-2 mt-16">
        <section className="card"><h2>Where chapters are now</h2><div className="dashboard-state-list">{data.states.map((entry) => <button key={entry.value} className="ghost dashboard-state" onClick={() => { setState(entry.value); setPage(1); }}><span>{entry.label}</span><strong>{entry.count}</strong></button>)}</div>{data.states.length === 0 && <div className="hint">No runs match these filters.</div>}</section>
        <section className="card"><h2>Operations</h2><div className="dashboard-state"><span>Worker</span><span className={data.queue.worker_alive ? "badge green" : "badge yellow"}>{data.queue.worker_alive ? "Running" : "Unavailable"}</span></div><div className="dashboard-state"><span>Batch chapter capacity</span><strong>{data.queue.batch_capacity ?? data.queue.capacity}</strong></div><div className="dashboard-state"><span>Batch Master capacity</span><strong>{data.queue.batch_master_capacity ?? "—"}</strong></div><div className="dashboard-state"><span>Failed / blocked</span><strong>{summary.failed} / {data.states.find((entry) => entry.value === "blocked")?.count ?? 0}</strong></div>
          {data.notifications && <div className="dashboard-state"><span>Email notifications</span><span className={data.notifications.configured ? "badge green" : "badge yellow"}>{data.notifications.configured ? "Configured" : "Sender setup required"}</span></div>}
          <p className="hint">Batch processing is asynchronous. A queued or waiting request is not a completed chapter. Recovery uses saved progress; the dashboard never starts work.</p>
        </section>
      </div>
      <section className="card mt-16"><h2>Per-person spend</h2><div className="table-scroll"><table><thead><tr><th>Started by</th><th>Runs</th><th>Known API spend</th><th>Batch</th><th>Standard</th></tr></thead><tbody>{data.users.map((user) => <tr key={user.email}><td>{user.email === "unknown" ? "Initiator not recorded" : user.email}</td><td>{user.runs}</td><td>{money(user.known_cost_usd)} {!user.cost_complete && <span className="badge yellow">Partial</span>}</td><td>{money(user.batch_cost_usd)}</td><td>{money(user.synchronous_cost_usd)}</td></tr>)}</tbody></table></div></section>
      <section className="card mt-16"><div className="row"><h2>Chapter run history</h2><div className="spacer" /><span className="hint">{data.total} sources · each job counted once</span></div><div className="table-scroll"><table><thead><tr><th>Chapter / source</th><th>Current stage</th><th>Started by / email</th><th>Known spend</th><th>Delivery cost</th></tr></thead><tbody>{data.items.map((row) => <RunRow key={row.job_id} row={row} />)}</tbody></table></div>{data.items.length === 0 && <div className="empty">No runs match these filters.</div>}<div className="row mt-12"><button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span className="hint">Page {data.page} of {data.total_pages}</span><button className="ghost" disabled={page >= data.total_pages} onClick={() => setPage(page + 1)}>Next</button></div></section>
      <div className="hint mt-12">{data.visibility}</div>
    </>}
  </>;
}
