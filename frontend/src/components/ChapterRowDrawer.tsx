import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, isNonTransientStatus, type StreamEvent } from "../api/client";
import type {
  ChapterBatchDetail,
  ChapterBatchLane,
  ChapterBatchPage,
  ChapterBatchRow,
  SourceArtifactFile,
} from "../types";
import { chapterLabel, stateLabel } from "../lib/chapterBatchState";
import ApiUsageSummary from "./ApiUsageSummary";
import ChapterRowUpload from "./ChapterRowUpload";

/** The run manifest's artifact kinds per lane, as the release writes them. */
const LANE_ARTIFACTS: Record<string, { concept: string; master: string }> = {
  post: { concept: "release_bulk_import", master: "release_master" },
  pre: { concept: "pre_release_bulk_import", master: "pre_release_master" },
};

const LANE_LABEL: Record<string, string> = {
  post: "Post-Learning",
  pre: "Pre-Learning",
};

const EVENT_POLL_MS = 4000;
const MAX_LINES = 60;

function laneLabel(lane: string): string {
  return LANE_LABEL[lane] ?? lane;
}

function artifact(
  files: SourceArtifactFile[] | undefined,
  kind: string | undefined,
): SourceArtifactFile | undefined {
  if (!files || !kind) return undefined;
  return files.find(
    (file) => file.kind === kind && file.action !== "post" && !file.disabled,
  );
}

function lineText(event: StreamEvent): string {
  switch (event.type) {
    case "log":
      return event.message;
    case "step":
      return `▸ ${event.label}`;
    case "progress":
      return event.label
        ? `${event.label} — ${Math.round(event.value * 100)}%`
        : `${Math.round(event.value * 100)}%`;
    case "error":
      return `Error: ${event.message}`;
    case "result":
      return "Run finished.";
    default:
      return "";
  }
}

/**
 * The expandable per-chapter detail.
 *
 * Everything expensive lives here rather than in the table row: the job
 * detail, the lane-by-lane downloads and reviewed-file uploads, the run
 * log tail, the usage summary, the pending decision and the blocked/error
 * text verbatim.
 *
 * The log tail keeps its OWN `seq` cursor and is started only when the
 * drawer mounts and stopped when it unmounts, so closing a drawer really
 * does stop its polling — one open drawer, one extra request stream, never
 * one per visible row.
 */
export default function ChapterRowDrawer({
  row,
  states,
  onRowUpdated,
}: {
  row: ChapterBatchRow;
  states?: ChapterBatchPage["states"];
  onRowUpdated: (row: ChapterBatchRow) => void;
}) {
  const [detail, setDetail] = useState<ChapterBatchDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [logError, setLogError] = useState<string | null>(null);
  const chapterId = row.chapter_id;

  useEffect(() => {
    let live = true;
    setDetail(null);
    setDetailError(null);
    api
      .chapterBatchDetail(chapterId)
      .then((next) => {
        if (live) setDetail(next);
      })
      .catch((e) => {
        if (live) setDetailError(String(e));
      });
    return () => {
      live = false;
    };
  }, [chapterId]);

  // The run journal tail. Its cursor is local to this drawer; the page's
  // list poll never carries log text.
  const cursor = useRef(0);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    cursor.current = 0;
    setEvents([]);
    setLogError(null);

    const tick = async () => {
      try {
        const batch = await api.chapterBatchEvents(chapterId, cursor.current);
        if (!live) return;
        cursor.current = batch.next;
        if (batch.events.length) {
          setEvents((prev) => [...prev, ...batch.events].slice(-MAX_LINES));
        }
        setLogError(null);
      } catch (e) {
        if (!live) return;
        setLogError(String(e));
        // The server answered and said no: asking again cannot change it.
        if (isNonTransientStatus(e)) return;
      }
      if (live) timer = setTimeout(tick, EVENT_POLL_MS);
    };
    void tick();

    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [chapterId]);

  const job = detail?.job ?? null;
  const files = job?.source_artifacts?.files;
  const lanes: ChapterBatchLane[] = row.lanes ?? [];
  const lines = events.map(lineText).filter(Boolean);

  return (
    <div className="chapter-drawer" data-testid={`chapter-${chapterId}-drawer`}>
      <div className="chapter-drawer-grid">
        <section className="chapter-drawer-col">
          <div className="step-subtitle">Lanes</div>
          {lanes.length === 0 && (
            <div className="hint">
              No lane is staged yet — Step 01 has not produced the Concept
              files for this chapter.
            </div>
          )}
          {lanes.map((lane) => {
            const kinds = LANE_ARTIFACTS[lane.lane];
            const conceptFile = artifact(files, kinds?.concept);
            const masterFile = artifact(files, kinds?.master);
            return (
              <div
                className={`chapter-lane${lane.available ? "" : " is-unavailable"}`}
                key={lane.lane}
                data-testid={`chapter-${chapterId}-lane-${lane.lane}`}
              >
                <div className="row">
                  <strong>{laneLabel(lane.lane)}</strong>
                  <span className="badge" data-testid={`chapter-${chapterId}-lane-${lane.lane}-concept`}>
                    Concept: {lane.concept}
                  </span>
                  <span className="badge" data-testid={`chapter-${chapterId}-lane-${lane.lane}-master`}>
                    Master: {lane.master}
                    {lane.master_version > 0 ? ` v${lane.master_version}` : ""}
                  </span>
                  {!lane.available && <span className="badge">not in this run</span>}
                </div>
                {lane.concept_reason && (
                  <div className="hint">{lane.concept_reason}</div>
                )}
                {lane.master_reason && (
                  <div className="hint">{lane.master_reason}</div>
                )}
                {lane.concept_reviewed && (
                  <div className="hint">
                    Reviewed Concept file on record
                    {lane.concept_reviewed_filename
                      ? `: ${lane.concept_reviewed_filename}`
                      : ""}
                  </div>
                )}
                <div className="row chapter-lane-files">
                  {conceptFile ? (
                    <a
                      className="button-link ghost"
                      href={conceptFile.download_url}
                      id={`chapter-${chapterId}-download-concept-${lane.lane}`}
                    >
                      Download Concept file
                    </a>
                  ) : (
                    <span className="hint">Concept file not available yet</span>
                  )}
                  {masterFile ? (
                    <a
                      className="button-link ghost"
                      href={masterFile.download_url}
                      id={`chapter-${chapterId}-download-master-${lane.lane}`}
                    >
                      Download Master file
                    </a>
                  ) : (
                    <span className="hint">Master file not available yet</span>
                  )}
                </div>
                <div className="row chapter-lane-files">
                  <ChapterRowUpload
                    chapterId={chapterId}
                    slot="concept"
                    lane={lane.lane}
                    disabled={!row.can.upload_concept}
                    onUploaded={onRowUpdated}
                    label={`Upload reviewed ${laneLabel(lane.lane)} Concept file`}
                    compact
                  />
                  <ChapterRowUpload
                    chapterId={chapterId}
                    slot="master"
                    lane={lane.lane}
                    disabled={!row.can.upload_master}
                    onUploaded={onRowUpdated}
                    label={`Upload reviewed ${laneLabel(lane.lane)} Master file`}
                    compact
                  />
                </div>
              </div>
            );
          })}

          {row.pending_decision && (
            <div
              className="chapter-decision"
              data-testid={`chapter-${chapterId}-pending-decision`}
            >
              <div className="step-subtitle">Waiting on a decision</div>
              <div className="hint">{row.pending_decision.kind}</div>
              <p className="mt-8">{row.pending_decision.question}</p>
              {row.pending_decision.companions > 0 && (
                <div className="hint">
                  {row.pending_decision.companions} companion item
                  {row.pending_decision.companions === 1 ? "" : "s"} travel with
                  this decision.
                </div>
              )}
              <div className="hint mt-8">
                The queue never answers a pause. Answer it on Build Concepts,
                then return the row to the queue.
              </div>
              <Link className="button-link ghost mt-8" to="/build-concepts">
                Open Build Concepts
              </Link>
            </div>
          )}

          {(row.blocked_kind || row.blocked_reason) && (
            <div
              className="chapter-blocked"
              role="status"
              data-testid={`chapter-${chapterId}-blocked`}
            >
              <div className="step-subtitle">
                Blocked{row.blocked_kind ? ` · ${row.blocked_kind}` : ""}
              </div>
              <div>{row.blocked_reason}</div>
            </div>
          )}

          {row.error_message && (
            <div
              className="error-box mt-12"
              data-testid={`chapter-${chapterId}-error`}
            >
              {row.error_message}
            </div>
          )}
          {row.queue.last_error && row.queue.last_error !== row.error_message && (
            <div className="error-box mt-12">{row.queue.last_error}</div>
          )}
        </section>

        <section className="chapter-drawer-col">
          <div className="step-subtitle">Run</div>
          <dl className="kv">
            <div>
              <dt>State</dt>
              <dd>{stateLabel(row.state, row.state_label, states)}</dd>
            </div>
            <div>
              <dt>Workflow marker</dt>
              <dd>{row.workflow_status || "—"}</dd>
            </div>
            <div>
              <dt>Job</dt>
              <dd className="mono">{row.job_id ?? "—"}</dd>
            </div>
            <div>
              <dt>Staged by</dt>
              <dd>{row.staged_by_email || "—"}</dd>
            </div>
            <div>
              <dt>Source file</dt>
              <dd>{row.source_filename || "—"}</dd>
            </div>
            <div>
              <dt>Source book</dt>
              <dd>{row.source_book || "—"}</dd>
            </div>
            <div>
              <dt>Attempt</dt>
              <dd>
                {row.queue.attempt || 0}
                {row.queue.max_attempts ? ` of ${row.queue.max_attempts}` : ""}
              </dd>
            </div>
            <div>
              <dt>Last act</dt>
              <dd>
                {row.last_actor_act || "—"}
                {row.last_actor_email ? ` · ${row.last_actor_email}` : ""}
              </dd>
            </div>
          </dl>

          {detailError && <div className="error-box mt-12">{detailError}</div>}

          {job?.openai_usage && (
            <ApiUsageSummary
              usage={job.openai_usage}
              compact
              cumulative
              fileLabel={chapterLabel(row)}
            />
          )}

          <div className="step-subtitle">Run log</div>
          {logError && <div className="error-box mb-8">{logError}</div>}
          {lines.length === 0 ? (
            <div className="hint">
              No journalled events for this chapter yet.
            </div>
          ) : (
            <pre
              className="chapter-log"
              data-testid={`chapter-${chapterId}-log`}
            >
              {lines.join("\n")}
            </pre>
          )}
        </section>
      </div>
    </div>
  );
}
