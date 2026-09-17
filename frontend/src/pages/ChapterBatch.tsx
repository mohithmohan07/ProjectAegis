import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, isNonTransientStatus } from "../api/client";
import { useAsync } from "../hooks";
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
  lanesToPublish,
  retryableFor,
  rowIsRetryable,
  rowNeedsPerson,
  rowProgressValue,
  rowShowsProgress,
  selectableFor,
} from "../lib/chapterBatchState";
import ChapterStateBadge from "../components/ChapterStateBadge";
import ChapterStepPips from "../components/ChapterStepPips";
import ChapterRowUpload, {
  BOOK_SOURCES_LIST_ID,
} from "../components/ChapterRowUpload";
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
  catalogue?: "active" | "history" | "all";
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

  const { board, grade, subject, q, state, page, catalogue = "active" } = query;

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
      // The spinner still has to be cleared, or a page opened in a
      // background tab reads "Loading chapters…" until it is looked at.
      if (typeof document !== "undefined" && document.hidden) {
        if (live) setLoading(false);
        return;
      }
      inFlight = true;
      try {
        const next = await api.chapterBatchList({
          board, grade, subject, q, state, page, catalogue,
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
  }, [board, grade, subject, q, state, page, catalogue, reloadToken]);

  const reload = useCallback(() => setReloadToken((t) => t + 1), []);

  const patchRows = useCallback((rows: ChapterBatchRow[]) => {
    if (rows.length === 0) return;
    setData((prev) => {
      if (!prev) return prev;
      const byId = new Map(
        rows.filter(Boolean).map((row) => [row.chapter_id, row]),
      );
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
    catalogue: params.get("catalogue") === "history" ? "history" : params.get("catalogue") === "all" ? "all" : "active",
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

/**
 * What the receipt calls the act. `/cancel` and `/retry` reuse the push
 * envelope and answer with an EMPTY `step`, so the page names what it sent.
 */
const ACTION_LABEL: Record<PushKind, string> = {
  step01: "Step 01",
  step02: "Step 02",
  publish: "Publish",
  cancel: "Cancel",
  retry: "Retry",
};

/** The next few half-hour slots, as the owner names them ("12:00 PM,
 * 12:30 PM, 1:00 PM..."). A slot is what makes a cohort START together: the
 * queue will not claim any of its tasks before it, so the first stage of
 * every chapter in the group arrives in one wave instead of trickling in
 * behind whoever was pushed first. */
export function nextSlots(from: Date = new Date(), count = 8): string[] {
  const out: string[] = [];
  const cursor = new Date(from.getTime());
  cursor.setSeconds(0, 0);
  cursor.setMinutes(cursor.getMinutes() >= 30 ? 60 : 30);
  for (let index = 0; index < count; index += 1) {
    out.push(cursor.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    cursor.setMinutes(cursor.getMinutes() + 30);
  }
  return out;
}

/** One displayed slot back to the instant the queue compares against. An
 * empty choice means "now", which is no gate at all. */
export function slotIso(label: string, from: Date = new Date()): string | undefined {
  if (!label.trim()) return undefined;
  const cursor = new Date(from.getTime());
  cursor.setSeconds(0, 0);
  cursor.setMinutes(cursor.getMinutes() >= 30 ? 60 : 30);
  for (let index = 0; index < 48; index += 1) {
    const shown = cursor.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (shown === label) return cursor.toISOString();
    cursor.setMinutes(cursor.getMinutes() + 30);
  }
  return undefined;
}

export default function ChapterBatch() {
  // Generation from this worklist is always an explicit discounted Batch API push.
  // Even a single chapter qualifies; upload is staging, never a paid action.
  const [slot, setSlot] = useState("");
  const { query, setFilter } = useChapterBatchFilters();
  const { data, error, loading, stopped, reload, patchRows } =
    useChapterBatchRows(query);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  // Every selected row's last projection, from whichever page it was
  // selected on. `rows` is only the page on screen, so filtering it left a
  // selection spanning pages counted in the action bar but never sent.
  const [selectedById, setSelectedById] = useState<Map<number, ChapterBatchRow>>(
    new Map(),
  );
  const [receipt, setReceipt] = useState<ChapterBatchPushResult | null>(null);
  const [receiptLabel, setReceiptLabel] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [pushing, setPushing] = useState<PushKind | null>(null);
  const [publishTarget, setPublishTarget] = useState<ChapterBatchRow[] | null>(null);
  const [openRow, setOpenRow] = useState<number | null>(null);
  const [searchDraft, setSearchDraft] = useState(query.q);

  // The known publications, for the staging form's suggestion list. It is
  // a suggestion only: the field stays free text, exactly as
  // `SourceBookInput` is on Build Concepts.
  const vocab = useAsync(() => api.vocab(), []);
  const bookSources = vocab.data?.book_sources ?? [];

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

  // Keep the remembered projections current for rows on the page in view.
  useEffect(() => {
    setSelectedById((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const row of rows) {
        if (selected.has(row.chapter_id) && prev.get(row.chapter_id) !== row) {
          next.set(row.chapter_id, row);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [rows, selected]);

  const selectedRows = useMemo(() => {
    const out: ChapterBatchRow[] = [];
    for (const id of selected) {
      const remembered = selectedById.get(id);
      if (remembered) out.push(remembered);
    }
    return out;
  }, [selected, selectedById]);

  const rememberRow = useCallback((row: ChapterBatchRow) => {
    setSelectedById((prev) => {
      const next = new Map(prev);
      next.set(row.chapter_id, row);
      return next;
    });
  }, []);

  const toggleRow = useCallback((chapterId: number) => {
    const row = rows.find((r) => r.chapter_id === chapterId);
    if (row) rememberRow(row);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(chapterId)) next.delete(chapterId);
      else next.add(chapterId);
      return next;
    });
  }, [rows, rememberRow]);

  const allOnPageSelected =
    rows.length > 0 && rows.every((row) => selected.has(row.chapter_id));

  const toggleAllOnPage = useCallback(() => {
    for (const row of rows) rememberRow(row);
    setSelected((prev) => {
      const next = new Set(prev);
      const every = rows.length > 0 && rows.every((row) => next.has(row.chapter_id));
      for (const row of rows) {
        if (every) next.delete(row.chapter_id);
        else next.add(row.chapter_id);
      }
      return next;
    });
  }, [rows, rememberRow]);

  const applyResult = useCallback(
    (result: ChapterBatchPushResult) => {
      setReceipt(result);
      // `row` is null for a chapter the server could no longer project
      // (an `unknown_chapter` refusal); there is nothing to patch.
      const fresh: ChapterBatchRow[] = [];
      for (const outcome of result.results) {
        if (outcome.row) fresh.push(outcome.row);
      }
      patchRows(fresh);
    },
    [patchRows],
  );

  const runPush = useCallback(
    async (kind: PushKind, targets: ChapterBatchRow[]) => {
      // A publish NAMES its lanes. The server never defaults a publication
      // target: an unnamed publish is refused outright with `no_lanes`
      // ("name the lanes to publish"), so sending the chapter id alone
      // would make every Publish button a no-op. The lanes are exactly the
      // ones the confirmation dialog counted — this chapter's own
      // available, not-yet-published lanes, never a pre+post pair.
      const rows =
        kind === "publish"
          ? targets
            .map((row) => ({
              row,
              lanes: lanesToPublish(row).map((lane) => lane.lane),
            }))
            .filter((entry) => entry.lanes.length > 0)
          : targets.map((row) => ({ row, lanes: [] as string[] }));
      if (rows.length === 0) return;
      // Publishing spends nothing and serializes on one workbook, so it
      // never joins a wave (register Q73); an ordinary push sends exactly
      // the request it always sent.
      const pushRows = (
        step: "step01" | "step02" | "publish",
        entries: typeof rows,
      ) => {
        const payload = entries.map((entry) =>
          step === "publish"
            ? { chapter_id: entry.row.chapter_id, lanes: entry.lanes }
            : { chapter_id: entry.row.chapter_id });
        if (step === "publish") {
          return api.chapterBatchPush(step, payload);
        }
        return api.chapterBatchPush(step, payload, {
          cohort: true, startAt: slotIso(slot),
        });
      };
      setPushing(kind);
      setActionError(null);
      setReceiptLabel(ACTION_LABEL[kind]);
      try {
        const ids = rows.map((entry) => entry.row.chapter_id);
        const result =
          kind === "cancel"
            ? await api.chapterBatchCancel(ids)
            : kind === "retry"
              ? await api.chapterBatchRetry(ids)
              : await pushRows(kind, rows);
        applyResult(result);
      } catch (e) {
        // A readable 409 detail (publication order, for one) is shown
        // exactly as the server wrote it.
        setActionError(String(e));
      } finally {
        setPushing(null);
      }
    },
    [applyResult, slot],
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
  const retryRows = retryableFor(selectedRows);

  return (
    <>
      <h1>Chapters</h1>
      <div className="subtitle">
        Upload sources, select chapters, then start a discounted Batch API run.
        Uploading only saves your files. Review the Concept files after Step 01,
        build Masters in Step 02, and publish when ready.
      </div>

      <div className="row chapter-workflow-guide">
        <span><strong>1</strong> Stage source files</span>
        <span><strong>2</strong> Select chapters together</span>
        <span><strong>3</strong> Start Batch API</span>
        <Link className="button-link ghost" to="/dashboard">Run &amp; cost dashboard</Link>
      </div>
      <div className="hint mt-8">You can close this page. Queued chapters and saved progress remain on the server.</div>

      {queue && (
        <div className="card chapter-queue" data-testid="chapter-queue-summary">
          <div className="row">
            <span className="badge accent">{queue.running} running</span>
            <span className="badge">{queue.queued} queued</span>
            <span className={queue.blocked ? "badge yellow" : "badge"}>
              {queue.blocked} blocked
            </span>
            <span className="badge">Batch capacity {queue.batch_capacity ?? queue.capacity}</span>
            {queue.batch_master_capacity !== undefined && <span className="badge">Master capacity {queue.batch_master_capacity}</span>}
            {queue.recovering !== undefined && queue.recovering > 0 && <span className="badge yellow">{queue.recovering} recovering</span>}
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
        <div className="row mt-12">
          <div className="field">
            <label className="field-label" htmlFor="cb-catalogue">Catalogue</label>
            <select id="cb-catalogue" value={query.catalogue} onChange={(e) => setFilter({ catalogue: e.target.value as ChapterBatchQuery["catalogue"] })}>
              <option value="active">Current chapters</option>
              <option value="history">Previous catalogue</option>
              <option value="all">Current and previous chapters</option>
            </select>
          </div>
          <span className="hint">Previous catalogue entries keep their runs and downloads.</span>
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
          <ChapterPushReceipt
            result={receipt}
            actionLabel={receiptLabel}
            onDismiss={() => setReceipt(null)}
          />
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
          <span className="badge accent" data-testid="push-cohort">Batch API · discounted</span>
          <label className="chapter-cohort" htmlFor="chapter-batch-slot">
            Start
            <select id="chapter-batch-slot" value={slot} onChange={(event) => setSlot(event.target.value)} data-testid="push-slot">
              <option value="">Now</option>
              {nextSlots().map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <span className="hint">Provider processing may take up to 24 hours per wave. No full-price fallback.</span>
          <div className="spacer" />
          <button
            disabled={step01Rows.length === 0 || pushing !== null}
            onClick={() => void runPush("step01", step01Rows)}
            data-testid="push-step01"
          >
            Start Batch · Step 01 ({step01Rows.length} of {selected.size})
          </button>
          <button
            disabled={step02Rows.length === 0 || pushing !== null}
            onClick={() => void runPush("step02", step02Rows)}
            data-testid="push-step02"
          >
            Start Batch · Step 02 ({step02Rows.length} of {selected.size})
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
            disabled={retryRows.length === 0 || pushing !== null}
            onClick={() => void runPush("retry", retryRows)}
            data-testid="push-retry"
          >
            Retry ({retryRows.length} of {selected.size})
          </button>
          <button
            className="ghost"
            disabled={cancelRows.length === 0 || pushing !== null}
            onClick={() => void runPush("cancel", cancelRows)}
            data-testid="push-cancel"
          >
            Cancel ({cancelRows.length} of {selected.size})
          </button>
          <button
            className="ghost"
            onClick={() => {
              setSelected(new Set());
              setSelectedById(new Map());
            }}
          >
            Clear
          </button>
        </div>
      )}

      {/* One page-level list of publications; every staging form points at
          it, so no row mints a second datalist under the same id. */}
      <datalist id={BOOK_SOURCES_LIST_ID}>
        {bookSources.map((source) => (
          <option key={source} value={source} />
        ))}
      </datalist>

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
          {row.catalogue_active === false && <span className="badge">Previous catalogue</span>}
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
          {row.queue.cohort_id && <div className="hint">Batch API{row.queue.start_after ? ` · scheduled ${new Date(row.queue.start_after).toLocaleString()}` : ""}</div>}
          {row.state === "recovering" && <div className="hint">Restoring saved progress. Do not start a duplicate run.</div>}
          {row.saved_progress != null && <div className="hint">Saved at {Math.round(row.saved_progress * 100)}%{row.saved_stage ? ` · ${row.saved_stage}` : ""}. Waiting to resume.</div>}
          {row.started_by_email && <div className="hint">Started by {row.started_by_email}</div>}
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
          {row.can.upload_source && row.job_id && (
            <ChapterRowUpload chapterId={row.chapter_id} slot="source" disabled={busy}
              sourceBook={row.source_book} onUploaded={onRowUpdated} label="Replace source PDF" scope="replace" compact />
          )}
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
        sourceBook={row.source_book}
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
        Start Batch · Step 01
      </button>
    );
  }
  // The reviewed-file uploads are per lane. With one available lane the
  // input sits in the row; with two, the row opens the drawer where each
  // lane is named — a defaulted lane would record the correction against
  // the OTHER lane, which is a silent wrong write, not an error.
  //
  // Order matters: at concept_review both upload_concept and step02 are
  // offered (Run Step 02 is the accept-unchanged shortcut), and at
  // master_review upload_master is the act the step needs. Listing step02
  // first hid the reviewed-Concept upload behind it, and listing
  // upload_concept before upload_master left a master_review row offering
  // a Concept upload the server refuses with 409 (verified audit,
  // 13 September 2026). The uploads come first; Step 02 stays one click
  // away in the action bar and the drawer.
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
  if (can.step02) {
    return (
      <button
        disabled={busy}
        onClick={() => onRun("step02")}
        id={`chapter-${row.chapter_id}-run-step02`}
      >
        Start Batch · Step 02
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
  if (can.upload_source) {
    // Last, so a chapter with real work to do still leads with that work.
    // But a chapter the server has settled as `dead` has NO other action —
    // every branch above is false for it — and replacing the source is the
    // one thing its own error message tells a person to do. Rendering the
    // dash there put the dead end back in the table after the server had
    // removed it, and the drawer was the only way through (owner report,
    // 15 September 2026).
    return (
      <ChapterRowUpload
        chapterId={row.chapter_id}
        slot="source"
        disabled={busy}
        onUploaded={onRowUpdated}
        sourceBook={row.source_book}
        label="Replace source PDF"
        compact
      />
    );
  }
  return <span className="hint">—</span>;
}
