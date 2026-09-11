import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useRunConsole } from "../RunConsole";
import {
  isMasterReviewStage,
  reviewWorkflowMarker,
  reviewWorkflowStatus,
} from "../lib/workflowSteps";
import type {
  MasterReviewLaneState,
  MasterReviewPublication,
  MasterReviewPublishResult,
  MasterReviewSubmitResult,
  SourceArtifactFile,
  UploadJob,
} from "../types";

export { isMasterReviewStage };

type Lane = "post" | "pre";

const LANES: Array<{
  lane: Lane;
  label: string;
  masterKind: string;
  conceptKind: string;
  publishKind: string;
}> = [
  {
    lane: "post",
    label: "Post-Learning",
    masterKind: "release_master",
    conceptKind: "release_bulk_import",
    publishKind: "database_upload",
  },
  {
    lane: "pre",
    label: "Pre-Learning",
    masterKind: "pre_release_master",
    conceptKind: "pre_release_bulk_import",
    publishKind: "pre_database_upload",
  },
];

type Busy = { kind: "upload" | "publish-concept" | "publish-master"; lane: Lane };

/**
 * Whether one lane's Concept file has been published, read off the run
 * manifest's publication entry. That entry is `disabled` once uploaded; a
 * `disabled_reason` instead means the lane was never staged.
 */
export type ConceptPublicationState =
  | { state: "published" }
  | { state: "available" }
  | { state: "unavailable"; reason: string };

export function conceptPublicationState(
  job: UploadJob,
  lane: Lane,
): ConceptPublicationState {
  const config = laneConfig(lane);
  const entry = fileOfKind(job, config.publishKind);
  if (!entry) {
    return {
      state: "unavailable",
      reason: `The run manifest has no ${config.label} publication entry `
        + "yet. Use Refresh outputs; if it stays missing, the lane's Concept "
        + "file was not staged.",
    };
  }
  if (entry.disabled) {
    return entry.disabled_reason
      ? { state: "unavailable", reason: entry.disabled_reason }
      : { state: "published" };
  }
  return { state: "available" };
}

function laneConfig(lane: Lane) {
  return LANES.find((item) => item.lane === lane)!;
}

function fileOfKind(job: UploadJob, kind: string): SourceArtifactFile | null {
  return job.source_artifacts?.files?.find((file) => file.kind === kind) ?? null;
}

function conceptDownloadUrl(job: UploadJob, lane: Lane): string {
  return fileOfKind(job, laneConfig(lane).conceptKind)?.download_url
    || api.conceptFileUrl(job.id, lane);
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

/** The backend records edits and omissions as LISTS of records; a bare count
 * is still accepted so an older or summarising payload renders. */
function count(value: unknown): number | null {
  if (Array.isArray(value)) return value.length;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** How many distinct questions a list of edit records touches. */
function editedQuestions(value: unknown): number {
  if (!Array.isArray(value)) return 0;
  const labels = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const label = (item as Record<string, unknown>).question_label;
    if (typeof label === "string" && label) labels.add(label);
  }
  return labels.size;
}

/** "3 fields in 2 questions" — one backend record per edited cell, each
 * naming its question. A bare count renders without the question half. */
function describeChanges(value: unknown): string {
  const fields = count(value) ?? 0;
  const questions = editedQuestions(value);
  return plural(fields, "field")
    + (questions > 0 ? ` in ${plural(questions, "question")}` : "");
}

function plural(value: number, noun: string): string {
  return `${value} ${noun}${value === 1 ? "" : "s"}`;
}

function laneStateFromMarker(job: UploadJob, lane: Lane): MasterReviewLaneState | null {
  const marker = reviewWorkflowMarker(job);
  const state = marker?.master_review?.[lane];
  return state && typeof state === "object" && !Array.isArray(state)
    ? state
    : null;
}

/**
 * Whether a reviewed Master file was really accepted for this lane. Only the
 * submit paths write `filename`/`uploaded_at`; publication merges the release
 * identity (which always carries `version`) into the same lane state, so a
 * version on its own proves nothing about an upload.
 */
function hasReceipt(state: MasterReviewLaneState | null | undefined): boolean {
  return Boolean(state && (
    typeof state.filename === "string"
    || typeof state.uploaded_at === "string"
  ));
}

function publicationOf(
  state: MasterReviewLaneState | null | undefined,
): MasterReviewPublication | null {
  const published = state?.published;
  return published && typeof published === "object" && !Array.isArray(published)
    ? published
    : null;
}

function receiptFromSubmit(
  response: MasterReviewSubmitResult,
  fallbackFilename: string,
): MasterReviewLaneState {
  const nested = response.master_review;
  return {
    filename: nested?.filename ?? response.filename ?? fallbackFilename,
    uploaded_at: nested?.uploaded_at,
    release_id: nested?.release_id ?? response.release_id,
    release_uid: nested?.release_uid ?? response.release_uid,
    version: nested?.version ?? response.version,
    changed_fields: nested?.changed_fields ?? response.changed_fields,
    omitted: nested?.omitted ?? response.omitted_questions,
    added: nested?.added ?? response.added_questions,
    readiness: nested?.readiness ?? response.readiness,
    issues: Array.isArray(nested?.issues)
      ? nested.issues
      : Array.isArray(response.issues) ? response.issues : [],
    status: nested?.status,
  };
}

function publicationFromPublish(
  response: MasterReviewPublishResult,
): MasterReviewPublication {
  const nested = publicationOf(response.master_review);
  return {
    uploaded_at: nested?.uploaded_at,
    database: nested?.database ?? response.database ?? null,
    cms_workbook: nested?.cms_workbook ?? response.cms_workbook ?? null,
    publication_status: response.publication_status,
  };
}

export type MasterPublicationState = "published" | "queued";

/**
 * A receipt whose CMS workbook append is `queued` is NOT a finished
 * publication: the backend committed the database half, recorded the queued
 * reason and expects the publish act to be repeated (it is idempotent and
 * converges). A receipt without a status is a historical one and complete.
 */
export function masterPublicationState(
  publication: MasterReviewPublication | null | undefined,
): MasterPublicationState | null {
  if (!publication) return null;
  const cms = publication.cms_workbook;
  const cmsStatus = cms && typeof cms === "object" && !Array.isArray(cms)
    ? String(cms.status ?? "")
    : "";
  const declared = typeof publication.publication_status === "string"
    ? publication.publication_status
    : "";
  return cmsStatus === "queued" || declared === "queued" ? "queued" : "published";
}

function queuedReason(publication: MasterReviewPublication): string {
  const cms = publication.cms_workbook;
  const reason = cms && typeof cms === "object" && !Array.isArray(cms)
    ? cms.queued_reason
    : undefined;
  return typeof reason === "string" && reason
    ? reason
    : "the database write is complete; the CMS workbook append has not finished";
}

function describeDatabase(publication: MasterReviewPublication): string {
  const db = publication.database ?? {};
  const parts: string[] = [];
  const questions = count(db.questions_created);
  const groups = count(db.groups_created);
  const labels = count(db.labels_reissued);
  if (questions !== null) parts.push(plural(questions, "question"));
  if (groups !== null) parts.push(plural(groups, "group"));
  if (labels !== null) parts.push(`${plural(labels, "label")} reissued`);
  return parts.length ? parts.join(", ") : "receipt recorded";
}

function describeCms(publication: MasterReviewPublication): string {
  const cms = publication.cms_workbook;
  if (!cms || typeof cms !== "object") return "";
  const parts = Object.entries(cms)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .map(([key, value]) => `${value} ${key.replace(/_/g, " ")}`);
  return parts.join(", ");
}

/**
 * Step 03 — review the two Master files, optionally upload reviewed copies,
 * then publish each lane explicitly: the Concept file first (the existing
 * per-lane release publication), then the Master file. Every publication
 * is confirmed, receipted and recorded in the console.
 */
export default function MasterReviewWorkflow({
  job,
  disabled = false,
  onJob,
}: {
  job: UploadJob;
  disabled?: boolean;
  onJob: (job: UploadJob) => void;
}) {
  const { restore, record } = useRunConsole();
  const [busy, setBusy] = useState<Busy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Short-lived fallbacks for an acknowledgement whose follow-up job read
  // failed; the durable marker wins as soon as a refresh delivers it.
  const [localReceipts, setLocalReceipts] =
    useState<Partial<Record<Lane, MasterReviewLaneState>>>({});
  const [localPublications, setLocalPublications] =
    useState<Partial<Record<Lane, MasterReviewPublication>>>({});
  const [localConceptPublished, setLocalConceptPublished] =
    useState<Partial<Record<Lane, boolean>>>({});

  useEffect(() => {
    setBusy(null);
    setError(null);
    setNotice(null);
    setLocalReceipts({});
    setLocalPublications({});
    setLocalConceptPublished({});
  }, [job.id]);

  useEffect(() => { restore(job); }, [job, restore]);

  const lanes = useMemo(() => LANES.map((config) => {
    const durable = laneStateFromMarker(job, config.lane);
    const receipt = hasReceipt(durable) ? durable : localReceipts[config.lane] ?? null;
    const publication = publicationOf(durable) ?? localPublications[config.lane] ?? null;
    const conceptState = conceptPublicationState(job, config.lane);
    const conceptPublished = conceptState.state === "published"
      || Boolean(localConceptPublished[config.lane])
      || Boolean(publication);
    return {
      ...config,
      master: fileOfKind(job, config.masterKind),
      receipt,
      publication,
      conceptState,
      conceptPublished,
    };
  }), [job, localReceipts, localPublications, localConceptPublished]);

  const locked = disabled || Boolean(busy) || Boolean(job.generation_running);
  const status = reviewWorkflowStatus(job);
  const published = status === "published";

  async function refreshJob(): Promise<{ fresh: UploadJob | null; error: unknown }> {
    try {
      const fresh = await api.getUploadJob("concepts", job.id);
      onJob(fresh);
      return { fresh, error: null };
    } catch (refreshError) {
      return { fresh: null, error: refreshError };
    }
  }

  async function uploadReviewedMaster(lane: Lane, file: File) {
    if (locked) return;
    const { label } = laneConfig(lane);
    setBusy({ kind: "upload", lane });
    setError(null);
    setNotice(null);
    record(job, `Receiving reviewed ${label} Master file: ${file.name}`);
    try {
      const response = await api.uploadReviewedMaster(job.id, lane, file);
      const receipt = receiptFromSubmit(response ?? { lane }, file.name);
      const issues = receipt.issues ?? [];
      record(
        job,
        `${label} Master file accepted`
          + (receipt.version != null ? ` as version ${receipt.version}` : "")
          + (receipt.readiness ? ` · readiness: ${receipt.readiness}` : "")
          + (issues.length ? ` · ${plural(issues.length, "issue")} listed` : ""),
        issues.length ? "warn" : "success",
      );
      for (const issue of issues) record(job, `${label} Master issue: ${issue}`, "warn");
      setLocalReceipts((current) => ({ ...current, [lane]: receipt }));
      const { error: refreshError } = await refreshJob();
      setNotice(`${label} reviewed Master file stored. Publish it below when you are ready.`);
      if (refreshError) {
        setError(`${label} Master upload was accepted, but the latest job status could not be refreshed: ${readableError(refreshError)}`);
      }
    } catch (uploadError) {
      record(job, `${label} Master upload failed: ${readableError(uploadError)}`, "error");
      await refreshJob();
      setError(`${label} reviewed Master file could not be uploaded: ${readableError(uploadError)}`);
    } finally {
      setBusy(null);
    }
  }

  async function publishConcept(lane: Lane) {
    if (locked) return;
    const { label } = laneConfig(lane);
    if (!window.confirm(
      `Publish the ${label} Concept file to the database and CMS now? `
      + "This is the authenticated publication act for this lane's concepts.",
    )) return;
    setBusy({ kind: "publish-concept", lane });
    setError(null);
    setNotice(null);
    record(job, `Publishing ${label} Concept file to the database and CMS…`);
    try {
      await api.uploadConceptRelease(job.id, lane);
      record(job, `${label} Concept file published to the database and CMS.`, "success");
      setLocalConceptPublished((current) => ({ ...current, [lane]: true }));
      const { error: refreshError } = await refreshJob();
      setNotice(`${label} Concept file published. The ${label} Master file can now be published.`);
      if (refreshError) {
        setError(`${label} Concept publication succeeded, but the latest job status could not be refreshed: ${readableError(refreshError)}`);
      }
    } catch (publishError) {
      record(job, `${label} Concept publication failed: ${readableError(publishError)}`, "error");
      await refreshJob();
      setError(`${label} Concept file could not be published: ${readableError(publishError)}`);
    } finally {
      setBusy(null);
    }
  }

  async function publishMaster(lane: Lane) {
    if (locked) return;
    const { label } = laneConfig(lane);
    if (!window.confirm(
      `Publish the ${label} Master file to the database and CMS now? `
      + "Questions and groups are written to the database and the CMS "
      + "workbook is produced in this act.",
    )) return;
    setBusy({ kind: "publish-master", lane });
    setError(null);
    setNotice(null);
    record(job, `Publishing ${label} Master file to the database and CMS…`);
    try {
      const response = await api.publishReviewedMaster(job.id, lane);
      const publication = publicationFromPublish(response ?? { lane });
      const cms = describeCms(publication);
      const queued = masterPublicationState(publication) === "queued";
      record(
        job,
        queued
          ? `${label} Master file written to the database `
            + `(${describeDatabase(publication)}); the CMS workbook append is `
            + `queued — ${queuedReason(publication)}`
          : `${label} Master file published · database: ${describeDatabase(publication)}`
            + (cms ? ` · CMS workbook: ${cms}` : ""),
        queued ? "warn" : "success",
      );
      setLocalPublications((current) => ({ ...current, [lane]: publication }));
      const { error: refreshError } = await refreshJob();
      setNotice(queued
        ? `${label} Master file was written to the database, but the CMS `
          + "workbook append is queued. Publish this lane again to complete it."
        : `${label} Master file published to the database and CMS.`);
      if (refreshError) {
        setError(`${label} Master publication was recorded, but the latest job status could not be refreshed: ${readableError(refreshError)}`);
      }
    } catch (publishError) {
      record(job, `${label} Master publication failed: ${readableError(publishError)}`, "error");
      await refreshJob();
      setError(`${label} Master file could not be published: ${readableError(publishError)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section
      className={`card mt-16 master-review-workflow${published ? " is-published" : ""}`}
      aria-labelledby="master-review-workflow-title"
    >
      <div className="row">
        <div>
          <div className="section-title" id="master-review-workflow-title">
            Step 03 · Review Master files & publish
          </div>
          <p className="muted mt-8">
            Step 2 is complete: the Master files were generated from your
            reviewed Concept files. Download and review each Master file. You
            may upload a reviewed copy; the upload records a review round and
            publishes nothing. Publication is explicit and per lane: publish
            the Concept file first, then the Master file. Each act writes to
            the database and the CMS only after you confirm it.
          </p>
        </div>
        <div className="spacer" />
        <span className={`badge ${published ? "green" : "yellow"}`}>
          {published
            ? "published"
            : job.generation_running
              ? "run active"
              : "Master files ready for review"}
        </span>
      </div>

      <div className="concept-review-lanes">
        {lanes.map(({
          lane, label, master, receipt, publication, conceptState, conceptPublished,
        }) => {
          const masterAvailable = Boolean(master && !master.disabled && master.download_url);
          const publicationState = masterPublicationState(publication);
          // A queued CMS append leaves the act unfinished: the lane stays
          // publishable so the reviewer can repeat it.
          const masterPublished = publicationState === "published";
          const inputId = `reviewed-master-input-${job.id}-${lane}`;
          const laneBusy = busy?.lane === lane ? busy.kind : null;
          const masterPublishBlocked = !conceptPublished
            ? `Publish the ${label} Concept file first.`
            : !masterAvailable && !receipt && !publication
              ? `No ${label} Master file is available to publish.`
              : "";
          return (
            <div className="concept-review-lane" key={lane} data-testid={`master-lane-${lane}`}>
              <div className="row">
                <strong>{label} Master File</strong>
                <div className="spacer" />
                {masterPublished
                  ? <span className="badge green">Master published</span>
                  : publicationState === "queued"
                    ? <span className="badge yellow">CMS append queued</span>
                    : receipt
                      ? <span className="badge green">Reviewed Master received</span>
                      : null}
                {conceptPublished && !masterPublished && (
                  <span className="badge accent">Concept file published</span>
                )}
              </div>

              <div className="row mt-8">
                {masterAvailable && master ? (
                  <a
                    className="button-link"
                    href={master.download_url}
                    download={master.filename || undefined}
                    aria-label={`Download the ${label} Master File`}
                    data-testid={`download-master-${lane}`}
                  >
                    Download {label} Master File
                  </a>
                ) : (
                  <button
                    className="ghost"
                    type="button"
                    disabled
                    aria-label={`Download the ${label} Master File`}
                    title={master?.disabled_reason || "Not available for this run."}
                    data-testid={`download-master-${lane}`}
                  >
                    Download {label} Master File
                  </button>
                )}
                <a
                  className="button-link ghost"
                  href={conceptDownloadUrl(job, lane)}
                  download={fileOfKind(job, laneConfig(lane).conceptKind)?.filename || undefined}
                  aria-label={`Download the ${label} Concept File`}
                  data-testid={`download-concept-${lane}`}
                >
                  Download {label} Concept File
                </a>
              </div>
              {!masterAvailable && (
                <div className="hint mt-4" role="note" data-testid={`master-unavailable-${lane}`}>
                  {master?.disabled_reason
                    || `The ${label} Master File is not available for this run.`}
                </div>
              )}
              {master?.note && masterAvailable && (
                <div className="hint mt-4" role="note">{master.note}</div>
              )}

              <div className="row mt-8">
                <input
                  id={inputId}
                  type="file"
                  accept=".xlsx"
                  disabled={locked}
                  data-testid={`reviewed-master-input-${lane}`}
                  onChange={(event) => {
                    const selected = event.target.files?.[0];
                    event.target.value = "";
                    if (selected) void uploadReviewedMaster(lane, selected);
                  }}
                  style={{ display: "none" }}
                />
                <button
                  className="ghost"
                  type="button"
                  disabled={locked}
                  onClick={() => document.getElementById(inputId)?.click()}
                  data-testid={`upload-reviewed-master-${lane}`}
                >
                  {laneBusy === "upload"
                    ? <><span className="spinner" aria-hidden="true" /> Uploading…</>
                    : `Upload reviewed ${label} Master file`}
                </button>
              </div>
              {receipt ? (
                <div className="hint mt-8 master-review-receipt" data-testid={`master-receipt-${lane}`}>
                  <span>
                    Accepted file: <strong>{receipt.filename || "reviewed Master workbook"}</strong>
                    {receipt.uploaded_at && <> · Accepted {formatTime(receipt.uploaded_at)}</>}
                    {receipt.version != null && <> · Version {receipt.version}</>}
                  </span>
                  <span>
                    Changes: {describeChanges(receipt.changed_fields)}
                    {" · "}{plural(count(receipt.omitted) ?? 0, "omitted question")}
                    {" · "}{plural(count(receipt.added) ?? 0, "added question")}
                    {receipt.readiness && <> · Readiness: <strong>{receipt.readiness}</strong></>}
                    {receipt.status && <> · State: {receipt.status}</>}
                  </span>
                  {Array.isArray(receipt.issues) && receipt.issues.length > 0 && (
                    <ul className="master-review-issues" data-testid={`master-issues-${lane}`}>
                      {receipt.issues.map((issue, index) => (
                        <li key={`${index}-${issue}`}>{issue}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <div className="hint mt-8">
                  {publication
                    ? `No reviewed ${label} Master file was uploaded; the `
                      + "generated Master File was published as is."
                    : `No reviewed Master uploaded; the generated ${label} `
                      + "Master File will be published as is."}
                </div>
              )}

              <div className="row mt-8">
                <button
                  className="ghost"
                  type="button"
                  disabled={locked || conceptPublished || conceptState.state !== "available"}
                  title={conceptState.state === "unavailable" ? conceptState.reason : undefined}
                  onClick={() => void publishConcept(lane)}
                  data-testid={`publish-concept-${lane}`}
                >
                  {laneBusy === "publish-concept"
                    ? <><span className="spinner" aria-hidden="true" /> Publishing…</>
                    : conceptPublished
                      ? "Concept file published"
                      : `Publish ${label} Concept file to database & CMS`}
                </button>
                <button
                  className="primary"
                  type="button"
                  disabled={locked || masterPublished || Boolean(masterPublishBlocked)}
                  title={masterPublishBlocked || undefined}
                  onClick={() => void publishMaster(lane)}
                  data-testid={`publish-master-${lane}`}
                >
                  {laneBusy === "publish-master"
                    ? <><span className="spinner" aria-hidden="true" /> Publishing…</>
                    : masterPublished
                      ? "Master file published"
                      : publicationState === "queued"
                        ? `Complete the ${label} Master publication`
                        : `Publish ${label} Master file to database & CMS`}
                </button>
              </div>
              {conceptState.state === "unavailable" && !conceptPublished && (
                <div className="hint mt-4" role="note">{conceptState.reason}</div>
              )}
              {masterPublishBlocked && !masterPublished && (
                <div className="hint mt-4" data-testid={`publish-master-blocked-${lane}`}>
                  {masterPublishBlocked}
                </div>
              )}
              {publication && (
                <div className="hint mt-8 master-review-receipt" role="status" data-testid={`master-publication-${lane}`}>
                  <span>
                    <strong>
                      {publicationState === "queued" ? "Publication incomplete" : "Published"}
                    </strong>
                    {publication.uploaded_at && <> {formatTime(publication.uploaded_at)}</>}
                    {" · "}Database: {describeDatabase(publication)}
                  </span>
                  {describeCms(publication) && (
                    <span>CMS workbook: {describeCms(publication)}</span>
                  )}
                  {publicationState === "queued" && (
                    <span data-testid={`master-publication-queued-${lane}`}>
                      CMS workbook append queued — {queuedReason(publication)}.
                      {" "}Publish this lane again to complete it.
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {notice && <div className="muted mt-12" role="status">{notice}</div>}
      <div className="hint mt-8">
        Reviewed Master files are XLSX workbooks downloaded from this step.
        Follow each act in the Activity log.
      </div>
      {error && <div className="error-box mt-12" role="alert">{error}</div>}
    </section>
  );
}
