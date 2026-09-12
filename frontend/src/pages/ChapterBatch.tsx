import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, isNonTransientStatus } from "../api/client";
import type {
  ChapterBatchPage,
  ChapterBatchRow,
  ChapterBatchPushResult,
  ChapterBatchStep,
} from "../types";
import {
  anyBusy,
  cancellableFor,
  chapterLabel,
  rowIsRetryable,
  rowNeedsPerson,
  rowProgressValue,
  rowShowsProgress,
  selectableFor,
} from "../lib/chapterBatchState";
import ChapterStateBadge from "../components/ChapterStateBadge";
import ChapterStepPips from "../components/ChapterStepPips";
import ChapterRowUpload from "../components/ChapterRowUpload";
import ChapterRowDrawer from "../components/ChapterRowDrawer";
import ChapterPublishDialog from "../components/ChapterPublishDialog";
import ChapterPushReceipt from "../components/ChapterPushReceipt";

/* =====================================================================
   The list poll
   ---------------------------------------------------------------------
   ONE request for every visible row, never one per row: a page of 25
   chapters polling individually would be 25 requests every few seconds
   against a machine that is also running two generations.

   Colocated here rather than in `src/hooks/` on purpose — this repo has
   no hooks directory, only `src/hooks.ts`, and adding a directory of the
   same name next to it makes `../hooks` ambiguous to read even where it
   still resolves.
   ===================================================================== */

/** While anything is in flight. Fast enough to watch, slow enough to be free. */
const BUSY_POLL_MS = 4000;
/** Nothing moving: the page is a worklist, not a dashboard. */
const IDLE_POLL_MS = 15000;

export interface ChapterBatchQuery {
  board: string;
  grade: string;
  subject: string;
  q: string;
  state: string;
  page: number;
}

export interface ChapterBatchRowsState {
  data: ChapterBatchPage | null;
  error: string | null;
  loading: boolean;
  /** Set when the server answered with a refusal no retry can change. */
  stopped: string | null;
  reload: () => void;
  /** Fold freshly projected rows (a push receipt, an upload) into the page. */
  patchRows: (rows: ChapterBatchRow[]) => void;
}

export function useChapterBatchRows(query: ChapterBatchQuery): ChapterBatchRowsState {
  const [data, setData] = useState<ChapterBatchPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [stopped, setStopped] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const dataRef = useRef<ChapterBatchPage | null>(null);

  const { board, grade, subject, q, state, page } = query;

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let halted = false;
    setLoading(true);
    setStopped(null);

    const interval = () => {
      const current = dataRef.current;
      if (!current) return BUSY_POLL_MS;
      const queue = current.queue;
      const queueBusy = Boolean(queue && (queue.running > 0 || queue.queued > 0));
      const items = Array.isArray(current.items) ? current.items : [];
      return anyBusy(items) || queueBusy ? BUSY_POLL_MS : IDLE_POLL_MS;
    };

    const schedule = () => {
      if (!live || halted) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void run(), interval());
    };

    const run = async () => {
      if (!live || halted || inFlight) return;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      // A hidden tab polls nothing; the visibility listener restarts it.
      if (typeof document !== "undefined" && document.hidden) return;
      inFlight = true;
      try {
        const next = await api.chapterBatchList({
          board, grade, subject, q, state, page,
        });
        if (!live) return;
        dataRef.current = next;
        setData(next);
        setError(null);
      } catch (e) {
        if (!live) return;
        setError(String(e));
        if (isNonTransientStatus(e)) {
          // The server answered and said no. Asking again cannot change
          // the answer, so stop for good instead of spinning.
          halted = true;
          setStopped(String(e));
          return;
        }
      } finally {
        inFlight = false;
        if (live) setLoading(false);
      }
      schedule();
    };

    const onVisibility = () => {
      if (!document.hidden) void run();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void run();

    return () => {
      live = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [board, grade, subject, q, state, page, reloadToken]);

  const reload = useCallback(() => setReloadToken((t) => t + 1), []);

  const patchRows = useCallback((rows: ChapterBatchRow[]) => {
    if (rows.length === 0) return;
    setData((prev) => {
      if (!prev) return prev;
      const byId = new Map(rows.map((row) => [row.chapter_id, row]));
      const next = {
        ...prev,
        items: prev.items.map((item) => byId.get(item.chapter_id) ?? item),
      };
      dataRef.current = next;
      return next;
    });
  }, []);

  return { data, error, loading, stopped, reload, patchRows };
}

/* =====================================================================
   Filters live in the URL so a teammate can share the exact worklist.
   ===================================================================== */

function useChapterBatchFilters() {
  const [params, setParams] = useSearchParams();
  const pageParam = Number(params.get("page") ?? "1");
  const query: ChapterBatchQuery = {
    board: params.get("board") ?? "",
    grade: params.get("grade") ?? "",
    subject: params.get("subject") ?? "",
    q: params.get("q") ?? "",
    state: params.get("state") ?? "",
    page: Number.isFinite(pageParam) && pageParam > 0 ? Math.floor(pageParam) : 1,
  };

  const setFilter = useCallback(
    (patch: Partial<ChapterBatchQuery>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(patch)) {
            const text = value === undefined || value === null ? "" : String(value);
            if (!text || text === "0" || (key === "page" && text === "1")) {
              next.delete(key);
            } else {
              next.set(key, text);
            }
          }
          // Any filter change resets paging unless the caller set it.
          if (!("page" in patch)) next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return { query, setFilter };
}

/* ===================================================================== */

type PushKind = ChapterBatchStep | "cancel" | "retry";

export default function ChapterBatch() {
  const { query, setFilter } = useChapterBatchFilters();
  const { data, error, loading, stopped, reload, patchRows } =
    useChapterBatchRows(query);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [receipt, setReceipt] = useState<ChapterBatchPushResult | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pushing, setPushing] = useState<PushKind | null>(null);
  const [publishTarget, setPublishTarget] = useState<ChapterBatchRow[] | null>(null);
  const [openRow, setOpenRow] = useState<number | null>(null);
  const [searchDraft, setSearchDraft] = useState(query.q);

  const rows = useMemo(() => data?.items ?? [], [data]);
  const states = data?.states;

  // Debounced search: typing is not a request per keystroke.
  const setFilterRef = useRef(setFilter);
  setFilterRef.current = setFilter;
  useEffect(() => {
    if (searchDraft === query.q) return;
    const timer = setTimeout(() => {
      setFilterRef.current({ q: searchDraft });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchDraft, query.q]);

  // Cascading board / grade / subject, read from the response's facets.
  // The facets describe the whole chapter catalogue, so narrowing one
  // select never empties the next one behind the current filter.
  const { boards, grades, subjects } = useMemo(() => {
    const facets = data?.facets;
    const triples = facets?.triples ?? [];
    const gradeSet = new Set<string>();
    const subjectSet = new Set<string>();
    for (const triple of triples) {
      if (query.board && triple.board !== query.board) continue;
      gradeSet.add(triple.grade);
      if (query.grade && triple.grade !== query.grade) continue;
      subjectSet.add(triple.subject);
    }
    return {
      boards: facets?.boards ?? [],
      grades: triples.length ? [...gradeSet].sort() : facets?.grades ?? [],
      subjects: triples.length ? [...subjectSet].sort() : facets?.subjects ?? [],
    };
  }, [data, query.board, query.grade]);

  const selectedRows = useMemo(
    () => rows.filter((row) => selected.has(row.chapter_id)),
    [rows, selected],
  );

  const toggleRow = useCallback((chapterId: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(chapterId)) next.delete(chapterId);
      else next.add(chapterId);
      return next;
    });
  }, []);

  const allOnPageSelected =
    rows.length > 0 && rows.every((row) => selected.has(row.chapter_id));

  const toggleAllOnPage = useCallback(() => {
    setSelected((prev) => {
      const next = new Set(prev);
      const every = rows.length > 0 && rows.every((row) => next.has(row.chapter_id));
      for (const row of rows) {
        if (every) next.delete(row.chapter_id);
        else next.add(row.chapter_id);
      }
      return next;
    });
  }, [rows]);

  const applyResult = useCallback(
    (result: ChapterBatchPushResult) => {
      setReceipt(result);
      patchRows(result.results.map((outcome) => outcome.row).filter(Boolean));
    },
    [patchRows],
  );

  const runPush = useCallback(
    async (kind: PushKind, targets: ChapterBatchRow[]) => {
      if (targets.length === 0) return;
      setPushing(kind);
      setActionError(null);
      try {
        const ids = targets.map((row) => row.chapter_id);
        const result =
          kind === "cancel"
            ? await api.chapterBatchCancel(ids)
            : kind === "retry"
              ? await api.chapterBatchRetry(ids)
              : await api.chapterBatchPush(
                kind,
                targets.map((row) => ({ chapter_id: row.chapter_id })),
              );
        applyResult(result);
      } catch (e) {
        // A readable 409 detail (publication order, for one) is shown
        // exactly as the server wrote it.
        setActionError(String(e));
      } finally {
        setPushing(null);
      }
    },
    [applyResult],
  );

  const confirmPublish = useCallback(async () => {
    const targets = publishTarget ?? [];
    await runPush("publish", targets);
    setPublishTarget(null);
  }, [publishTarget, runPush]);

  const queue = data?.queue;
  const step01Rows = selectableFor("step01", selectedRows);
  const step02Rows = selectableFor("step02", selectedRows);
  const publishRows = selectableFor("publish", selectedRows);
  const cancelRows = cancellableFor(selectedRows);

  return (
    <>
      <h1>Chapters</h1>
      <div className="subtitle">
        Every chapter in the catalogue. Stage a source PDF, push the batch,
        pick the Concept files up when Step 01 finishes, upload the reviewed
        files, then publish the reviewed Masters to the database and the CMS
        workbook.
      </div>

      {queue && (
        <div className="card chapter-queue" data-testid="chapter-queue-summary">
          <div className="row">
            <span className="badge accent">{queue.running} running</span>
            <span className="badge">{queue.queued} queued</span>
            <span className={queue.blocked ? "badge yellow" : "badge"}>
              {queue.blocked} blocked
            </span>
            <span className="badge">capacity {queue.capacity}</span>
            <div className="spacer" />
            {queue.worker_alive ? (
              <span className="badge green" data-testid="chapter-worker-alive">
                Worker running
              </span>
            ) : (
              <span className="badge red" data-testid="chapter-worker-dead">
                No worker
              </span>
            )}
          </div>
          {!queue.worker_alive && (
            <div className="error-box mt-8" role="status">
              No worker is draining this queue. Pushed chapters will sit at
              "queued" and nothing will run until a worker is back.
            </div>
          )}
        </div>
      )}

      <div className="card mt-16 chapter-filters">
        <div className="grid cols-4">
          <div className="field">
            <label className="field-label" htmlFor="cb-board">Board</label>
            <select
              id="cb-board"
              value={query.board}
              onChange={(e) => setFilter({ board: e.target.value, grade: "", subject: "" })}
            >
              <option value="">All boards</option>
              {boards.map((b) => <option key={b}>{b}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cb-grade">Grade</label>
            <select
              id="cb-grade"
              value={query.grade}
              onChange={(e) => setFilter({ grade: e.target.value, subject: "" })}
            >
              <option value="">All grades</option>
              {grades.map((g) => <option key={g}>{g}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cb-subject">Subject</label>
            <select
              id="cb-subject"
              value={query.subject}
              onChange={(e) => setFilter({ subject: e.target.value })}
            >
              <option value="">All subjects</option>
              {subjects.map((s) => <option key={s}>{s}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cb-state">State</label>
            <select
              id="cb-state"
              value={query.state}
              onChange={(e) => setFilter({ state: e.target.value })}
            >
              <option value="">Any state</option>
              {(states ?? []).map((entry) => (
                <option key={entry.value} value={entry.value}>{entry.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="field chapter-search">
          <label className="field-label" htmlFor="cb-search">Search chapters</label>
          <input
            id="cb-search"
            type="search"
            placeholder="Chapter name, code or unit"
            value={searchDraft}
            onChange={(e) => setSearchDraft(e.target.value)}
          />
        </div>
      </div>

      {stopped ? (
        <div className="error-box mt-16" data-testid="chapter-poll-stopped">
          {stopped}
          <div className="hint mt-8">
            Updates stopped: the server refused this request, so retrying it
            cannot change the answer. Sign in again or reload the page.
          </div>
        </div>
      ) : (
        error && <div className="error-box mt-16">{error}</div>
      )}

      {actionError && <div className="error-box mt-16">{actionError}</div>}

      {receipt && (
        <div className="mt-16">
          <ChapterPushReceipt result={receipt} onDismiss={() => setReceipt(null)} />
        </div>
      )}

      <div className="card mt-16">
        <div className="table-wrap">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col" className="chapter-col-select">
                    <input
                      type="checkbox"
                      aria-label="Select every chapter on this page"
                      checked={allOnPageSelected}
                      onChange={toggleAllOnPage}
                    />
                  </th>
                  <th scope="col">Chapter</th>
                  <th scope="col">State</th>
                  <th scope="col">Steps</th>
                  <th scope="col">Next action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const open = openRow === row.chapter_id;
                  return (
                    <ChapterRowView
                      key={row.chapter_id}
                      row={row}
                      states={states}
                      open={open}
                      selected={selected.has(row.chapter_id)}
                      busy={pushing !== null}
                      onToggleSelect={() => toggleRow(row.chapter_id)}
                      onToggleOpen={() =>
                        setOpenRow(open ? null : row.chapter_id)}
                      onRowUpdated={(next) => patchRows([next])}
                      onRun={(step) => void runPush(step, [row])}
                      onRetry={() => void runPush("retry", [row])}
                      onCancel={() => void runPush("cancel", [row])}
                      onPublish={() => setPublishTarget([row])}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {rows.length === 0 && !loading && (
          <div className="empty" data-testid="chapter-empty">
            {query.board || query.grade || query.subject || query.q || query.state
              ? "No chapter matches these filters."
              : "No chapters are loaded. Import the syllabus workbooks on the Database page first."}
          </div>
        )}
        {rows.length === 0 && loading && (
          <div className="empty">Loading chapters…</div>
        )}
      </div>

      {data && data.total_pages > 1 && (
        <div className="row mt-12">
          <button
            className="ghost"
            disabled={query.page <= 1}
            onClick={() => setFilter({ page: query.page - 1 })}
          >
            Previous
          </button>
          <span className="muted">
            Page {data.page} of {data.total_pages} · {data.total} chapters
          </span>
          <button
            className="ghost"
            disabled={query.page >= data.total_pages}
            onClick={() => setFilter({ page: query.page + 1 })}
          >
            Next
          </button>
          <div className="spacer" />
          <button className="ghost" onClick={reload}>Refresh</button>
        </div>
      )}

      {selected.size > 0 && (
        <div className="chapter-actionbar" data-testid="chapter-actionbar">
          <strong>{selected.size} selected</strong>
          <div className="spacer" />
          <button
            disabled={step01Rows.length === 0 || pushing !== null}
            onClick={() => void runPush("step01", step01Rows)}
            data-testid="push-step01"
          >
            Run Step 01 ({step01Rows.length} of {selected.size})
          </button>
          <button
            disabled={step02Rows.length === 0 || pushing !== null}
            onClick={() => void runPush("step02", step02Rows)}
            data-testid="push-step02"
          >
            Run Step 02 ({step02Rows.length} of {selected.size})
          </button>
          <button
            disabled={publishRows.length === 0 || pushing !== null}
            onClick={() => setPublishTarget(publishRows)}
            data-testid="push-publish"
          >
            Publish ({publishRows.length} of {selected.size})
          </button>
          <button
            className="ghost"
            disabled={cancelRows.length === 0 || pushing !== null}
            onClick={() => void runPush("cancel", cancelRows)}
            data-testid="push-cancel"
          >
            Cancel ({cancelRows.length} of {selected.size})
          </button>
          <button className="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {publishTarget && (
        <ChapterPublishDialog
          rows={publishTarget}
          busy={pushing === "publish"}
          onConfirm={() => void confirmPublish()}
          onCancel={() => setPublishTarget(null)}
        />
      )}
    </>
  );
}

function ChapterRowView({
  row,
  states,
  open,
  selected,
  busy,
  onToggleSelect,
  onToggleOpen,
  onRowUpdated,
  onRun,
  onRetry,
  onCancel,
  onPublish,
}: {
  row: ChapterBatchRow;
  states?: ChapterBatchPage["states"];
  open: boolean;
  selected: boolean;
  busy: boolean;
  onToggleSelect: () => void;
  onToggleOpen: () => void;
  onRowUpdated: (row: ChapterBatchRow) => void;
  onRun: (step: ChapterBatchStep) => void;
  onRetry: () => void;
  onCancel: () => void;
  onPublish: () => void;
}) {
  const label = chapterLabel(row);
  const showProgress = rowShowsProgress(row);
  return (
    <>
      <tr
        data-testid={`chapter-row-${row.chapter_id}`}
        data-state={row.state}
        className={rowNeedsPerson(row) ? "chapter-row needs-person" : "chapter-row"}
      >
        <td className="chapter-col-select">
          <input
            type="checkbox"
            id={`chapter-${row.chapter_id}-select`}
            aria-label={`Select ${label}`}
            checked={selected}
            onChange={onToggleSelect}
          />
        </td>
        <td>
          <button
            type="button"
            className="chapter-title-button"
            id={`chapter-${row.chapter_id}-toggle`}
            aria-expanded={open}
            onClick={onToggleOpen}
          >
            {label}
          </button>
          <div className="hint">
            <span className="mono">{row.chapter_code}</span>
            {` · ${row.board} · Grade ${row.grade} · ${row.subject}`}
            {row.unit ? ` · ${row.unit}` : ""}
          </div>
          {row.source_filename && (
            <div className="hint">{row.source_filename}</div>
          )}
        </td>
        <td>
          <ChapterStateBadge row={row} states={states} />
          {showProgress && (
            <div
              className="progress-track mt-8"
              data-testid={`chapter-${row.chapter_id}-progress`}
            >
              <div
                className="progress-fill progress-active"
                style={{ width: `${Math.round(rowProgressValue(row) * 100)}%` }}
              />
            </div>
          )}
          {showProgress && row.stage && <div className="hint">{row.stage}</div>}
        </td>
        <td>
          <ChapterStepPips row={row} />
        </td>
        <td>
          <PrimaryAction
            row={row}
            busy={busy}
            onRowUpdated={onRowUpdated}
            onRun={onRun}
            onRetry={onRetry}
            onCancel={onCancel}
            onPublish={onPublish}
            onOpenDrawer={onToggleOpen}
          />
        </td>
      </tr>
      {open && (
        <tr className="chapter-drawer-row">
          <td colSpan={5}>
            <ChapterRowDrawer
              row={row}
              states={states}
              onRowUpdated={onRowUpdated}
            />
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Exactly ONE action per row — the one the server's `can` map says is
 * available, in workflow order. Everything else lives in the drawer, so
 * the table stays legible on a phone instead of growing a wall of
 * trailing action columns.
 */
function PrimaryAction({
  row,
  busy,
  onRowUpdated,
  onRun,
  onRetry,
  onCancel,
  onPublish,
  onOpenDrawer,
}: {
  row: ChapterBatchRow;
  busy: boolean;
  onRowUpdated: (row: ChapterBatchRow) => void;
  onRun: (step: ChapterBatchStep) => void;
  onRetry: () => void;
  onCancel: () => void;
  onPublish: () => void;
  onOpenDrawer: () => void;
}) {
  const can = row.can;
  const reviewLanes = (row.lanes ?? []).filter((lane) => lane.available);

  if (can.upload_source && !row.job_id) {
    return (
      <ChapterRowUpload
        chapterId={row.chapter_id}
        slot="source"
        disabled={busy}
        onUploaded={onRowUpdated}
        label="Upload source PDF"
        compact
      />
    );
  }
  if (can.step01) {
    return (
      <button
        disabled={busy}
        onClick={() => onRun("step01")}
        id={`chapter-${row.chapter_id}-run-step01`}
      >
        Run Step 01
      </button>
    );
  }
  if (can.step02) {
    return (
      <button
        disabled={busy}
        onClick={() => onRun("step02")}
        id={`chapter-${row.chapter_id}-run-step02`}
      >
        Run Step 02
      </button>
    );
  }
  // The reviewed-file uploads are per lane. With one available lane the
  // input sits in the row; with two, the row opens the drawer where each
  // lane is named — a defaulted lane would record the correction against
  // the OTHER lane, which is a silent wrong write, not an error.
  if (can.upload_concept) {
    return reviewLanes.length === 1 ? (
      <ChapterRowUpload
        chapterId={row.chapter_id}
        slot="concept"
        lane={reviewLanes[0].lane}
        disabled={busy}
        onUploaded={onRowUpdated}
        label="Upload reviewed Concept file"
        compact
      />
    ) : (
      <button
        className="ghost"
        onClick={onOpenDrawer}
        id={`chapter-${row.chapter_id}-open-concept-upload`}
      >
        Upload reviewed Concept files
      </button>
    );
  }
  if (can.upload_master) {
    return reviewLanes.length === 1 ? (
      <ChapterRowUpload
        chapterId={row.chapter_id}
        slot="master"
        lane={reviewLanes[0].lane}
        disabled={busy}
        onUploaded={onRowUpdated}
        label="Upload reviewed Master file"
        compact
      />
    ) : (
      <button
        className="ghost"
        onClick={onOpenDrawer}
        id={`chapter-${row.chapter_id}-open-master-upload`}
      >
        Upload reviewed Master files
      </button>
    );
  }
  if (can.publish) {
    return (
      <button
        disabled={busy}
        onClick={onPublish}
        id={`chapter-${row.chapter_id}-publish`}
      >
        Publish
      </button>
    );
  }
  if (rowIsRetryable(row)) {
    return (
      <button
        className="ghost"
        disabled={busy}
        onClick={onRetry}
        id={`chapter-${row.chapter_id}-retry`}
      >
        Retry
      </button>
    );
  }
  if (can.cancel) {
    return (
      <button
        className="ghost"
        disabled={busy}
        onClick={onCancel}
        id={`chapter-${row.chapter_id}-cancel`}
      >
        Cancel
      </button>
    );
  }
  return <span className="hint">—</span>;
}
