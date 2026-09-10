import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useRunConsole } from "../RunConsole";
import type {
  CorrectedConceptInput,
  ReviewWorkflow,
  SourceArtifactFile,
  UploadJob,
} from "../types";

type Lane = "post" | "pre";

const LANES: Array<{ lane: Lane; label: string; kind: string }> = [
  { lane: "post", label: "Post-Learning", kind: "release_bulk_import" },
  { lane: "pre", label: "Pre-Learning", kind: "pre_release_bulk_import" },
];

const REVIEW_WAIT_STATUSES = new Set([
  "pending_review",
  "reviewed",
  "master_building",
  "master_failed",
]);

/**
 * The explicit review boundary is intentionally separate from legacy
 * `generated`/`released` jobs.  This lets historical runs keep their existing
 * four-output and CMS publication controls while new jobs wait after Concept
 * files are staged.
 */
export function isConceptReviewWaiting(job: UploadJob | Record<string, unknown>): boolean {
  const record = job as Record<string, unknown>;
  const workflow = workflowState(record);
  return Boolean(
    workflow?.status
    && REVIEW_WAIT_STATUSES.has(workflow.status.toLowerCase()),
  );
}

function workflowState(record: Record<string, unknown>): ReviewWorkflow | null {
  // UploadJob responses project this as `review_workflow`. The stream's
  // terminal payload currently names the same object `concept_review`; both
  // are the backend's exact Q41 projection, and neither is a status alias.
  const raw = record.review_workflow ?? record.concept_review;
  return raw && typeof raw === "object" && !Array.isArray(raw)
    ? raw as ReviewWorkflow
    : null;
}

function inputForLane(job: UploadJob | Record<string, unknown>, lane: Lane): unknown {
  const record = job as unknown as Record<string, unknown>;
  const workflow = workflowState(record);
  const nested = workflow?.corrected_inputs?.[lane];
  return hasInput(nested) ? nested : null;
}

function hasInput(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value === "string") return value.trim().length > 0;
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<CorrectedConceptInput> & Record<string, unknown>;
  return candidate.accepted === true
    || typeof candidate.filename === "string"
    || ["uploaded", "accepted", "ready", "received"].includes(
      String(candidate.status ?? "").toLowerCase(),
    );
}

function artifactForLane(job: UploadJob, lane: Lane): SourceArtifactFile | null {
  const files = job.source_artifacts?.files ?? [];
  const config = LANES.find((item) => item.lane === lane)!;
  return files.find((file) => file.kind === config.kind)
    ?? files.find((file) => {
      const kind = file.kind.toLowerCase();
      return kind.includes(lane) && (
        kind.includes("concept") || kind.includes("bulk_import")
      );
    })
    ?? null;
}

function artifactDownloadUrl(job: UploadJob, lane: Lane): string {
  return artifactForLane(job, lane)?.download_url
    || api.conceptFileUrl(job.id, lane);
}

function artifactFilename(job: UploadJob, lane: Lane): string | undefined {
  return artifactForLane(job, lane)?.filename || undefined;
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Review the two Concept files, optionally upload corrected copies, and then
 * explicitly continue the same durable job into Master generation. Uploading
 * a correction never publishes anything to the CMS.
 */
export default function ConceptReviewWorkflow({
  job,
  disabled = false,
  onJob,
  onMasterGenerated,
}: {
  job: UploadJob;
  disabled?: boolean;
  onJob: (job: UploadJob) => void;
  onMasterGenerated?: (job: UploadJob) => void;
}) {
  const { run } = useRunConsole();
  const [busyLane, setBusyLane] = useState<Lane | null>(null);
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [localInputs, setLocalInputs] = useState<Partial<Record<Lane, {
    filename: string;
    acceptedAt: string;
    status: string;
  }>>>({});

  useEffect(() => {
    setBusyLane(null);
    setContinuing(false);
    setError(null);
    setNotice(null);
    setLocalInputs({});
  }, [job.id]);

  const inputMeta = useMemo(() => ({
    // The durable projection is authoritative after refresh. Local state is
    // only a short-lived fallback for an acknowledgement response that did
    // not include the freshly projected lane metadata yet.
    post: inputMetaFor(inputForLane(job, "post")) || localInputs.post,
    pre: inputMetaFor(inputForLane(job, "pre")) || localInputs.pre,
  }), [job, localInputs]);

  async function uploadCorrection(lane: Lane, file: File) {
    if (disabled || continuing || busyLane) return;
    setBusyLane(lane);
    setError(null);
    setNotice(null);
    try {
      // This route stages the corrected input only. In particular, do not
      // call the legacy uploadEditedWorkbook route here: that route performs
      // an authenticated CMS publication for historical releases.
      const response = await api.uploadCorrectedConceptInput(job.id, lane, file);
      const responseRecord = response && typeof response === "object"
        && !Array.isArray(response) ? response as Record<string, unknown> : null;
      let fresh: UploadJob | null = isUploadJob(response)
        ? response
        : null;
      let refreshError: unknown = null;
      if (!fresh) {
        try {
          fresh = await api.getUploadJob("concepts", job.id);
        } catch (error) {
          // The submit response has already committed the review round. Keep
          // its lane projection available when the follow-up GET is the part
          // that fails (for example, a transient 500).
          refreshError = error;
        }
      }
      // The API has validated and persisted this file. Prefer its durable
      // projection for filename/time/state; the selected browser filename is
      // only a fallback until a projection is available.
      const durableMeta = fresh ? inputMetaFor(inputForLane(fresh, lane)) : undefined;
      const responseMeta = inputMetaFor(responseRecord?.corrected_input)
        || (responseRecord
          ? inputMetaFor(inputForLane(responseRecord, lane))
          : undefined);
      if (fresh) onJob(fresh);
      if (durableMeta || responseMeta) {
        setLocalInputs((current) => ({
          ...current,
          [lane]: {
            filename: durableMeta?.filename
              || responseMeta?.filename
              || file.name,
            acceptedAt: durableMeta?.acceptedAt || responseMeta?.acceptedAt || "",
            status: durableMeta?.status || responseMeta?.status || "accepted",
          },
        }));
      }
      const added = Number(responseRecord?.added_rows ?? 0);
      setNotice(`${laneLabel(lane)} corrected Concept File uploaded for Master generation.`
        + (added > 0 ? ` ${added} new concept${added === 1 ? "" : "s"} accepted.` : ""));
      if (refreshError) {
        setError(`${laneLabel(lane)} upload was accepted, but the latest job status could not be refreshed: ${readableError(refreshError)}`);
      }
    } catch (uploadError) {
      // A provider or proxy can report an error after the review round has
      // committed. Refresh once and adopt only the lane state returned by the
      // server; otherwise leave any previously accepted lane (such as Pre)
      // untouched and do not infer acceptance from the failed request.
      try {
        const fresh = await api.getUploadJob("concepts", job.id);
        onJob(fresh);
        const durableMeta = inputMetaFor(inputForLane(fresh, lane));
        if (durableMeta) {
          setLocalInputs((current) => ({ ...current, [lane]: durableMeta }));
        }
      } catch {
        // Keep the original upload error visible when recovery also fails.
      }
      setError(`${laneLabel(lane)} corrected input could not be uploaded: ${readableError(uploadError)}`);
    } finally {
      setBusyLane(null);
    }
  }

  async function generateMaster() {
    if (disabled || continuing || busyLane || job.generation_running) return;
    setContinuing(true);
    setError(null);
    setNotice(null);
    try {
      await run<Record<string, unknown>>(
        "Master Files — generating from reviewed Concepts",
        api.paths.masterGenerate(job.id),
        {},
        {
          cumulative: true,
          continuation: true,
          resumed: true,
          filename: job.filename,
          fileLabel: "Source file",
          initialUsage: job.openai_usage,
        },
        {
          module: "concepts",
          jobId: job.id,
          operation: "master",
          recoverResult: async () => {
            const finished = await api.getUploadJob("concepts", job.id);
            return {
              status: finished.status,
              review_workflow: finished.review_workflow,
              source_artifacts: finished.source_artifacts,
              openai_usage: finished.openai_usage,
            };
          },
        },
      );
      const fresh = await api.getUploadJob("concepts", job.id);
      onJob(fresh);
      onMasterGenerated?.(fresh);
      const status = fresh.review_workflow?.status ?? fresh.status;
      setNotice(status === "released" || fresh.status === "released"
        ? "Master Files are ready. Review and publish each released Master explicitly."
        : "Master generation finished. Refresh the output cards to download the files.");
    } catch (generationError) {
      let fresh: UploadJob | null = null;
      try {
        fresh = await api.getUploadJob("concepts", job.id);
        onJob(fresh);
      } catch {
        // Keep the stream error visible when a best-effort refresh also fails.
      }
      setError(`Master generation could not continue: ${readableError(generationError)}`);
    } finally {
      setContinuing(false);
    }
  }

  const waiting = isConceptReviewWaiting(job);
  const masterRunning = job.generation_running
    || job.review_workflow?.status === "master_building";

  return (
    <section className="card concept-review-workflow" aria-labelledby="concept-review-workflow-title">
      <div className="row">
        <div>
          <div className="section-title" id="concept-review-workflow-title">
            Review Concept Files before generating Masters
          </div>
          <p className="muted mt-8">
            Download both Concept Files and review every question, topic,
            concept, Type and Case. In the Post-Learning file you may omit,
            add or move questions when correcting it. Upload the same edited
            Excel only when you need one; a file you leave unchanged is
            retained as generated input. You may also add relevant prerequisite
            concepts to the Pre-Learning file, including when it was empty.
            The original chapter and source selection stays locked; keep the
            concept ID when renaming or moving a concept. Context, images and
            other dependencies stay attached unless you deliberately edit them.
          </p>
        </div>
        <div className="spacer" />
        <span className={`badge ${waiting ? "yellow" : masterRunning ? "accent" : "green"}`}>
          {waiting ? "waiting for review" : masterRunning ? "Master generation running" : "Concept review"}
        </span>
      </div>

      <div className="concept-review-lanes">
        {LANES.map(({ lane, label }) => {
          const artifact = artifactForLane(job, lane);
          const input = inputMeta[lane];
          const inputId = `corrected-concept-input-${job.id}-${lane}`;
          return (
            <div className="concept-review-lane" key={lane}>
              <div className="row">
                <strong>{label} Concept File</strong>
                <div className="spacer" />
                {input && <span className="badge green">Corrected input received</span>}
              </div>
              <div className="row mt-8">
                <a
                  className="button-link"
                  href={artifactDownloadUrl(job, lane)}
                  download={artifactFilename(job, lane)}
                  aria-label={`Download the ${label} Concept File`}
                  data-testid={`download-concept-${lane}`}
                >
                  Download {label} Concept File
                </a>
                <input
                  id={inputId}
                  type="file"
                  accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  disabled={disabled || continuing || Boolean(busyLane)}
                  data-testid={`corrected-input-${lane}`}
                  onChange={(event) => {
                    const selected = event.target.files?.[0];
                    event.target.value = "";
                    if (selected) void uploadCorrection(lane, selected);
                  }}
                  style={{ display: "none" }}
                />
                <button
                  className="ghost"
                  type="button"
                  disabled={disabled || continuing || Boolean(busyLane)}
                  onClick={() => document.getElementById(inputId)?.click()}
                  data-testid={`upload-corrected-${lane}`}
                >
                  {busyLane === lane
                    ? <><span className="spinner" aria-hidden="true" /> Uploading…</>
                    : `Upload edited ${label} Excel`}
                </button>
              </div>
              {input ? (
                <div className="hint mt-8">
                  <span data-testid={`accepted-input-${lane}`}>
                    Accepted file: <strong>{input.filename}</strong>
                  </span>
                  {input.acceptedAt && (
                    <span> · Accepted {formatAcceptedAt(input.acceptedAt)}</span>
                  )}
                  {input.status && <span> · State: {input.status}</span>}
                  <div>Using this edited Concept workbook for the next Master run.</div>
                </div>
              ) : (
                <div className="hint mt-8">No replacement uploaded; the generated Concept File will be used.</div>
              )}
              {artifact?.note && <div className="hint mt-4">{artifact.note}</div>}
            </div>
          );
        })}
      </div>

      <div className="concept-review-continue mt-16">
        <div>
          <strong>Continue only when both Concept Files are reviewed</strong>
          <div className="hint mt-4">
            This starts the Master stages on the same job and uses every
            corrected input already accepted above. Pre-Learning questions are
            generated automatically from the reviewed Pre concepts. It does
            not publish to the CMS.
          </div>
        </div>
        <button
          className="primary"
          type="button"
          disabled={disabled || continuing || Boolean(busyLane) || masterRunning}
          onClick={() => void generateMaster()}
          data-testid="generate-master"
        >
          {continuing || masterRunning
            ? <><span className="spinner" aria-hidden="true" /> Generating Master Files…</>
            : "Generate Master Files"}
        </button>
      </div>

      {notice && <div className="muted mt-12" role="status">{notice}</div>}
      {error && <div className="error-box mt-12" role="alert">{error}</div>}
    </section>
  );
}

function laneLabel(lane: Lane): string {
  return lane === "post" ? "Post-Learning" : "Pre-Learning";
}

function inputMetaFor(value: unknown): {
  filename: string;
  acceptedAt: string;
  status: string;
} | undefined {
  if (typeof value === "string" && value.trim()) {
    return { filename: value, acceptedAt: "", status: "accepted" };
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  const filename = typeof record.filename === "string" ? record.filename : "";
  if (!filename && !hasInput(value)) return undefined;
  return {
    filename: filename || "edited Concept workbook",
    acceptedAt: typeof record.uploaded_at === "string"
      ? record.uploaded_at
      : typeof record.accepted_at === "string" ? record.accepted_at : "",
    status: typeof record.status === "string" ? record.status : "accepted",
  };
}

function formatAcceptedAt(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function isUploadJob(value: unknown): value is UploadJob {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return Number.isInteger(record.id) && typeof record.filename === "string";
}
