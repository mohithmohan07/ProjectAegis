import type {
  BlueprintBatch,
  BoardNode,
  ChapterDetail,
  PreviewResult,
  PromptInfo,
  Question,
  Session,
  Stats,
  TagResult,
  UploadJob,
  Vocab,
  WorkbookEntry,
  WorkbookResult,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const baseHeaders: Record<string, string> =
    init?.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const res = await fetch(`${BASE}${path}`, {
    ...init,
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
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type StreamEvent =
  | { type: "log"; level?: string; message: string; ts?: number }
  | { type: "step"; label: string; ts?: number }
  | { type: "progress"; value: number; label?: string; ts?: number }
  | { type: "result"; data: unknown; ts?: number }
  | { type: "error"; message: string; trace?: string; ts?: number }
  | { type: "heartbeat"; ts?: number };

/**
 * POST to an NDJSON progress endpoint, dispatching each event to `onEvent` as
 * it streams in. Resolves with the final `result` payload, or throws on an
 * `error` event / non-2xx response (e.g. a 400 precheck).
 */
export async function streamNdjson<T = unknown>(
  path: string,
  init: RequestInit,
  onEvent: (evt: StreamEvent) => void,
): Promise<T> {
  const baseHeaders: Record<string, string> =
    init.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...baseHeaders, ...(init.headers as Record<string, string> | undefined) },
  });
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
  handle(buffer);

  if (errored) throw new Error((errored as { message: string }).message || "stream error");
  return result as T;
}

export const api = {
  base: BASE,
  health: () => http<{ status: string }>("/health"),

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
  createWorkbookUrl: (subject: string, board: string, grade: string, mode: "blank" | "content") =>
    `${BASE}/data/workbook/new?${new URLSearchParams({ subject, board, grade, mode })}`,
  importWorkbook: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return http<Record<string, number>>("/data/import", { method: "POST", body: fd });
  },
  resetData: () =>
    http<{ status: string; chapters: number; questions: number }>(
      "/data/reset",
      { method: "POST" },
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

  // Streaming endpoint paths (consumed via streamNdjson / RunConsole)
  paths: {
    assessmentConvert: (id: number) => `/build-assessments/uploads/${id}/convert`,
    assessmentGenerate: (id: number) => `/build-assessments/uploads/${id}/generate`,
    sessionGenerate: (id: number) => `/build-assessments/sessions/${id}/generate`,
    conceptConvert: (id: number) => `/build-concepts/uploads/${id}/convert`,
    postLearningGenerate: (id: number) => `/build-concepts/post-learning/uploads/${id}/generate`,
    preLearningGenerate: (id: number) => `/build-concepts/pre-learning/uploads/${id}/generate`,
    preLearningFromExisting: "/build-concepts/pre-learning/from-existing",
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
  preLearningUpload: (file: File, sourceBook = "") => {
    const fd = new FormData();
    fd.append("file", file);
    return http<UploadJob>(
      `/build-concepts/pre-learning/uploads?source_book=${encodeURIComponent(sourceBook)}`,
      { method: "POST", body: fd },
    );
  },
  preLearningGenerateFromUpload: (jobId: number, target_chapter_id: number) =>
    http<Record<string, unknown>>(
      `/build-concepts/pre-learning/uploads/${jobId}/generate`,
      { method: "POST", body: JSON.stringify({ target_chapter_id }) },
    ),
  preLearningFromExisting: (chapter_ids: number[], source_book = "") =>
    http<Record<string, unknown>>("/build-concepts/pre-learning/from-existing", {
      method: "POST",
      body: JSON.stringify({ chapter_ids, source_book }),
    }),

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
};
