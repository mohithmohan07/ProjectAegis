import type {
  BlueprintBatch,
  BoardNode,
  ChapterDetail,
  AuthConfig,
  AuthSession,
  OpenAIUsage,
  PreviewResult,
  PromptInfo,
  Question,
  Session,
  Stats,
  ConceptRevision,
  ConceptRevisionList,
  ReleaseInstructionBody,
  ReleaseManualEditBody,
  ReleaseReviewLane,
  ReleaseReviewView,
  ResumableCheckpoints,
  SemanticDecisionSubmission,
  SemanticDecisionSubmissionResult,
  TagResult,
  UploadJob,
  Vocab,
  WorkbookEntry,
  WorkbookResult,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

/**
 * Fired on `window` when the server rejects the user session cookie.
 * AuthProvider listens and flips the app back to the sign-in gate, so an
 * expired session (12h TTL — shorter than a long overnight run) surfaces as
 * "sign in again" instead of raw 401 banners on every panel. Admin-token
 * failures are deliberately excluded: they don't mean the user signed out.
 */
export const SESSION_EXPIRED_EVENT = "aegis:session-expired";

// A failed HTTP call carries its status so callers can tell a
// non-transient refusal (401 session expiry, 403, 404 after a data
// reset) from a flaky network — the difference between "retry quietly"
// and "stop polling and say why". The message contract is unchanged.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// Statuses no retry loop should ever spin on: the server answered and
// said no; asking again cannot change the answer.
export function isNonTransientStatus(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status;
  return (
    status === 401 || status === 403 || status === 404 || status === 410
  );
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const baseHeaders: Record<string, string> =
    init?.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { ...baseHeaders, ...(init?.headers as Record<string, string> | undefined) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    if (res.status === 401 && detail === "authentication required") {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type ModelProviderInfo = {
  provider: string;
  model: string;
  openai_available: boolean;
  gemini_available: boolean;
  gemini_model: string;
  openai_model: string;
  note: string;
};

/* `seq` is the durable run journal's monotonic cursor: it rides both the
   live stream and the run-events catch-up reads, so a client that saw an
   event once can recognise (and skip) it arriving again. */
export type StreamEvent =
  | { type: "log"; level?: string; message: string; ts?: number; seq?: number }
  | { type: "step"; label: string; ts?: number; seq?: number }
  | { type: "progress"; value: number; label?: string; ts?: number; seq?: number }
  | { type: "usage"; data: OpenAIUsage; ts?: number; seq?: number }
  | { type: "result"; data: unknown; ts?: number; seq?: number }
  | { type: "error"; message: string; trace?: string; openai_usage?: OpenAIUsage; ts?: number; seq?: number }
  | { type: "heartbeat"; ts?: number; seq?: number };

/**
 * The connection died mid-stream — the network dropped, the tab lost
 * connectivity, or the response body was cut off before a terminal event.
 * Distinct from a server-reported error so callers can re-attach to a run
 * that is still executing on the server instead of declaring it failed.
 */
export class StreamTransportError extends Error {
  cause?: unknown;

  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "StreamTransportError";
    this.cause = cause;
  }

  /** Page error boxes render String(err); a person should read what
   * happened, not the class name. */
  toString(): string {
    return this.message;
  }
}

/**
 * POST to an NDJSON progress endpoint, dispatching each event to `onEvent` as
 * it streams in. Resolves with the final `result` payload, or throws on an
 * `error` event / non-2xx response (e.g. a 400 precheck). A network-level
 * failure — before, during, or by truncation of the stream — throws
 * `StreamTransportError` instead, because the server-side run continues
 * without the connection.
 */
export async function streamNdjson<T = unknown>(
  path: string,
  init: RequestInit,
  onEvent: (evt: StreamEvent) => void,
): Promise<T> {
  const baseHeaders: Record<string, string> =
    init.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include",
      headers: { ...baseHeaders, ...(init.headers as Record<string, string> | undefined) },
    });
  } catch (transportFailure) {
    throw new StreamTransportError(
      "network connection failed before the stream started",
      transportFailure,
    );
  }
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: T | undefined;
  let errored: { message: string } | null = null;

  const handle = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let evt: StreamEvent;
    try {
      evt = JSON.parse(trimmed) as StreamEvent;
    } catch {
      return;
    }
    onEvent(evt);
    if (evt.type === "result") result = evt.data as T;
    else if (evt.type === "error") errored = { message: evt.message };
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) >= 0) {
        handle(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 1);
      }
    }
  } catch (transportFailure) {
    throw new StreamTransportError(
      "network connection lost while the run was streaming",
      transportFailure,
    );
  }
  handle(buffer);

  if (errored) throw new Error((errored as { message: string }).message || "stream error");
  if (result === undefined) {
    // The stream ended without a terminal `result` or `error` event: the
    // connection was cut, not the run.
    throw new StreamTransportError(
      "the stream ended before the run reported a result",
    );
  }
  return result as T;
}

export const api = {
  base: BASE,
  health: () => http<{ status: string }>("/health"),
  authConfig: () => http<AuthConfig>("/auth/config"),
  authMe: () => http<AuthSession>("/auth/me"),
  authGoogle: (credential: string, csrfToken: string) =>
    http<AuthSession>("/auth/google", {
      method: "POST",
      body: JSON.stringify({
        credential,
        csrf_token: csrfToken,
      }),
    }),
  authLogout: () =>
    http<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  // Store apps only: swaps the one-time ticket from the system-browser
  // Google flow (aegis://auth?ticket=…) for the normal session cookie.
  authNativeExchange: (ticket: string) =>
    http<AuthSession>("/auth/native/exchange", {
      method: "POST",
      body: JSON.stringify({ ticket }),
    }),

  // Directory / database
  tree: () => http<BoardNode[]>("/directory/tree"),
  chapter: (id: number) => http<ChapterDetail>(`/directory/chapters/${id}`),
  vocab: () => http<Vocab>("/directory/vocab"),
  stats: () => http<Stats>("/directory/stats"),
  questions: (params: Record<string, string> = {}) =>
    http<Question[]>(`/data/questions?${new URLSearchParams(params)}`),
  exportUrl: (scope: "all" | "output") => `${BASE}/data/export?scope=${scope}`,
  exportQuestionsUrl: (ids: number[]) =>
    `${BASE}/data/export/questions?ids=${ids.join(",")}`,
  exportConceptsUrl: (ids: number[]) =>
    `${BASE}/data/export/concepts?ids=${ids.join(",")}`,
  inventoryCsvUrl: (jobId: number) =>
    `${BASE}/build-concepts/uploads/${jobId}/inventory.csv`,
  checkpointUrl: (jobId: number) =>
    `${BASE}/build-concepts/uploads/${jobId}/checkpoint`,
  createWorkbookUrl: (subject: string, board: string, grade: string, mode: "blank" | "content") =>
    `${BASE}/data/workbook/new?${new URLSearchParams({ subject, board, grade, mode })}`,
  importWorkbook: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http<Record<string, number>>("/data/import", { method: "POST", body: fd });
  },
  resetData: (adminToken: string) =>
    http<{ status: string; chapters: number; questions: number }>(
      "/data/reset",
      {
        method: "POST",
        headers: { "X-Admin-Token": adminToken },
      },
    ),
  // Admin — editable prompts (password-gated)
  adminLogin: (password: string) =>
    http<{ token: string }>("/admin/login", {
      method: "POST", body: JSON.stringify({ password }),
    }),
  adminListPrompts: (token: string) =>
    http<{ categories: string[]; prompts: PromptInfo[] }>("/admin/prompts", {
      headers: { "X-Admin-Token": token },
    }),
  adminUpdatePrompt: (token: string, key: string, text: string) =>
    http<PromptInfo>(`/admin/prompts/${key}`, {
      method: "PUT",
      headers: { "X-Admin-Token": token },
      body: JSON.stringify({ text }),
    }),
  adminResetPrompt: (token: string, key: string) =>
    http<PromptInfo>(`/admin/prompts/${key}/reset`, {
      method: "POST",
      headers: { "X-Admin-Token": token },
    }),

  // Upload staging / conversion (split from processing)
  getUploadJob: (module: "assessments" | "concepts", jobId: number) =>
    http<UploadJob>(
      `/build-${module === "assessments" ? "assessments" : "concepts"}/uploads/${jobId}`),
  /** The run's journaled events after a cursor: lossless catch-up for a
   * client that was away (frozen tab, dropped connection). Includes the
   * terminal result/error event, so a run can FINISH through this. */
  getRunEvents: (
    module: "assessments" | "concepts", jobId: number, after: number,
  ) =>
    http<{ events: StreamEvent[]; next: number; running: boolean }>(
      `/build-${module === "assessments" ? "assessments" : "concepts"}`
      + `/uploads/${jobId}/run-events?after=${after}`),
  replaceAssessmentFile: (jobId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http<UploadJob>(`/build-assessments/uploads/${jobId}/file`,
      { method: "PUT", body: fd });
  },
  replaceConceptFile: (jobId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http<UploadJob>(`/build-concepts/uploads/${jobId}/file`,
      { method: "PUT", body: fd });
  },
  importConceptCheckpoint: (file: File, learningKind: "post" | "pre") => {
    const fd = new FormData();
    fd.append("file", file);
    return http<UploadJob>(
      `/build-concepts/checkpoints/import?learning_kind=${learningKind}`,
      {
        method: "POST",
        body: fd,
      },
    );
  },
  clearConceptCheckpoint: (jobId: number) =>
    http<UploadJob>(`/build-concepts/uploads/${jobId}/checkpoint`, {
      method: "DELETE",
    }),
  getModelProvider: () =>
    http<ModelProviderInfo>("/build-concepts/model-provider"),
  setModelProvider: (provider: string) =>
    http<ModelProviderInfo>("/build-concepts/model-provider", {
      method: "PUT",
      body: JSON.stringify({ provider }),
    }),
  resumableConceptCheckpoints: (learningKind: "post" | "pre") =>
    http<ResumableCheckpoints>(
      `/build-concepts/checkpoints/resumable?learning_kind=${learningKind}`,
    ),
  submitConceptDecision: (
    jobId: number,
    decisionId: string,
    submission: SemanticDecisionSubmission,
  ) =>
    http<SemanticDecisionSubmissionResult>(
      `/build-concepts/uploads/${jobId}/decisions/${encodeURIComponent(decisionId)}`,
      {
        method: "POST",
        body: JSON.stringify(submission),
      },
    ),
  releaseConceptOutput: (jobId: number) =>
    http<UploadJob>(`/build-concepts/uploads/${jobId}/release`, {
      method: "POST",
    }),
  /** Submit one reviewer instruction. Rounds are unlimited. */
  submitConceptRevision: (jobId: number, instruction: string) =>
    http<ConceptRevision>(`/build-concepts/uploads/${jobId}/revisions`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),
  listConceptRevisions: (jobId: number) =>
    http<ConceptRevisionList>(`/build-concepts/uploads/${jobId}/revisions`),
  /** Absolute URL of the released workbook, for a plain browser download. */
  conceptReleaseUrl: (jobId: number) =>
    `${BASE}/build-concepts/uploads/${jobId}/release.xlsx`,
  /** Released rows in the canonical Bulk Import workbook format. */
  conceptReleaseBulkImportUrl: (jobId: number) =>
    `${BASE}/build-concepts/uploads/${jobId}/release-bulk-import.xlsx`,
  /**
   * Publish ONE staged release lane to the database (Rule G: a separate,
   * explicit, authenticated act).
   *
   * `lane` is required, and deliberately has no default. One job stages two
   * releases — Outputs 01/02 under "post" and Outputs 03/04 under "pre" —
   * and the server's own `lane` query defaults to "post", so a call that
   * omits it does not fail, it publishes the OTHER lane. A wrong-lane
   * publication is an authenticated write the reviewer did not authorise,
   * which is worse than an error; making the argument mandatory means the
   * type checker asks the question at every call site.
   */
  uploadConceptRelease: (jobId: number, lane: "post" | "pre") =>
    http<Record<string, unknown>>(
      `/build-concepts/uploads/${jobId}/upload-release?lane=${lane}`,
      { method: "POST" },
    ),

  /** The reviewer's locally edited Concept workbook: applied as one
   * recorded review round, then published to the CMS in the same act. */
  uploadEditedWorkbook: (jobId: number, lane: "post" | "pre", file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http<Record<string, unknown>>(
      `/build-concepts/uploads/${jobId}/upload-edited-workbook?lane=${lane}`,
      { method: "POST", body: fd },
    );
  },

  // Release review (step 9): read the staged release, edit it in place or
  // via a plain-language instruction. Every write carries the
  // staged_release_uid it was read from, so a concurrent edit 409s instead
  // of being silently overwritten. `lane` is mandatory for the same reason
  // it is on uploadConceptRelease: a defaulted lane writes the OTHER release.
  getReleaseReview: (jobId: number, lane: ReleaseReviewLane) =>
    http<ReleaseReviewView>(
      `/build-concepts/uploads/${jobId}/release-review?lane=${lane}`,
    ),
  submitReleaseManualEdit: (jobId: number, body: ReleaseManualEditBody) =>
    http<ReleaseReviewView>(
      `/build-concepts/uploads/${jobId}/release-review/manual-edit`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  applyReleaseInstruction: (jobId: number, body: ReleaseInstructionBody) =>
    http<ReleaseReviewView>(
      `/build-concepts/uploads/${jobId}/release-review/apply-instruction`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  // Streaming endpoint paths (consumed via streamNdjson / RunConsole)
  paths: {
    assessmentConvert: (id: number) => `/build-assessments/uploads/${id}/convert`,
    assessmentGenerate: (id: number) => `/build-assessments/uploads/${id}/generate`,
    sessionGenerate: (id: number) => `/build-assessments/sessions/${id}/generate`,
    conceptConvert: (id: number) => `/build-concepts/uploads/${id}/convert`,
    postLearningGenerate: (id: number) => `/build-concepts/post-learning/uploads/${id}/generate`,
    workbookGenerate: "/workbooks/generate",
  },

  uploadSyllabus: (files: File[]) => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    return http<Record<string, unknown>>("/data/syllabus/upload", {
      method: "POST",
      body: fd,
    });
  },
  importSyllabus: () =>
    http<Record<string, unknown>>("/data/syllabus/import", { method: "POST" }),

  // Build Assessments — concept mapping
  createSession: (scope_type: string, scope_ids: number[]) =>
    http<Session>("/build-assessments/sessions", {
      method: "POST",
      body: JSON.stringify({ scope_type, scope_ids }),
    }),
  getSession: (id: number) => http<Session>(`/build-assessments/sessions/${id}`),
  addBatch: (sessionId: number, batch: Omit<BlueprintBatch, "id">) =>
    http<BlueprintBatch>(`/build-assessments/sessions/${sessionId}/batches`, {
      method: "POST",
      body: JSON.stringify(batch),
    }),
  generateSession: (sessionId: number) =>
    http<{ session_id: number; created: number; pipeline: Record<string, unknown> }>(
      `/build-assessments/sessions/${sessionId}/generate`,
      { method: "POST" },
    ),

  // Build Assessments — upload
  createAssessmentUpload: (uploadType: string, file: File, sourceBook = "") => {
    const fd = new FormData();
    fd.append("file", file);
    const qs = new URLSearchParams({ upload_type: uploadType, source_book: sourceBook });
    return http<UploadJob>(`/build-assessments/uploads?${qs}`, {
      method: "POST",
      body: fd,
    });
  },
  setTextbookMode: (jobId: number, mode: string) =>
    http<UploadJob>(`/build-assessments/uploads/${jobId}/textbook-mode`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  setDeposit: (jobId: number, scope_type: string, scope_ids: number[]) =>
    http<UploadJob>(`/build-assessments/uploads/${jobId}/deposit`, {
      method: "POST",
      body: JSON.stringify({ scope_type, scope_ids }),
    }),
  generateFromUpload: (jobId: number, question_type: string) =>
    http<{ job_id: number; created: number; pipeline: Record<string, unknown> }>(
      `/build-assessments/uploads/${jobId}/generate`,
      { method: "POST", body: JSON.stringify({ question_type }) },
    ),

  // Build Concepts
  postLearningUpload: (file: File, sourceBook = "") => {
    const fd = new FormData();
    fd.append("file", file);
    return http<UploadJob>(
      `/build-concepts/post-learning/uploads?source_book=${encodeURIComponent(sourceBook)}`,
      { method: "POST", body: fd },
    );
  },
  postLearningGenerate: (jobId: number, target_chapter_id: number) =>
    http<Record<string, unknown>>(
      `/build-concepts/post-learning/uploads/${jobId}/generate`,
      { method: "POST", body: JSON.stringify({ target_chapter_id }) },
    ),

  // Create Workbooks (revision-PDF generator)
  workbookSubjects: () =>
    http<{ subjects: string[]; live: boolean }>("/workbooks/subjects"),
  generateWorkbook: (file: File, subject: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("subject", subject);
    return http<WorkbookResult>("/workbooks/generate", { method: "POST", body: fd });
  },
  workbookLibrary: () => http<WorkbookEntry[]>("/workbooks/library"),
  workbookFileUrl: (rel: string) => `${BASE}/workbooks/file?rel=${encodeURIComponent(rel)}`,

  // Tagging (many-to-many) + import preview
  tagQuestionToConcept: (questionId: number, concept_id: number) =>
    http<TagResult>(`/tagging/questions/${questionId}/tag-to-concept`, {
      method: "POST",
      body: JSON.stringify({ concept_id }),
    }),
  tagConceptToTopic: (conceptId: number, topic_id: number) =>
    http<TagResult>(`/tagging/concepts/${conceptId}/tag-to-topic`, {
      method: "POST",
      body: JSON.stringify({ topic_id }),
    }),
  preview: (question_ids: number[], concept_ids: number[]) =>
    http<PreviewResult>("/tagging/preview", {
      method: "POST",
      body: JSON.stringify({ question_ids, concept_ids }),
    }),

  // Assessment releases: dual projections of one immutable snapshot (spec §16).
  getAssessmentRelease: (id: number) =>
    http<AssessmentRelease>(`/build-assessments/releases/${id}`),
  getAssessmentReleaseIssues: (id: number) =>
    http<AssessmentReleaseIssues>(`/build-assessments/releases/${id}/issues`),
  releaseConceptsUrl: (id: number) =>
    `${BASE}/build-assessments/releases/${id}/concepts.xlsx`,
  releaseMasterUrl: (id: number) =>
    `${BASE}/build-assessments/releases/${id}/master.xlsx`,
  uploadReleaseMaster: (id: number) =>
    http<AssessmentReleaseUploadResult>(
      `/build-assessments/releases/${id}/upload-to-database`,
      { method: "POST" },
    ),
};

export interface AssessmentRelease {
  id: number;
  release_uid: string;
  version: number;
  state: string;
  readiness: string;
  concept_snapshot_sha256: string;
  workbook_sha256s: Record<string, string>;
  published: boolean;
  uploaded: boolean;
  created_at: string;
}

export interface AssessmentReleaseIssues {
  readiness: string;
  payload_errors: string[];
  read_back: { concepts_errors?: string[]; master_errors?: string[] };
  issues: { unplaced?: { candidate_id: string; question_label: string; reason: string }[] };
}

export interface AssessmentReleaseUploadResult {
  release_uid: string;
  version: number;
  groups_created: number;
  questions_created: number;
}