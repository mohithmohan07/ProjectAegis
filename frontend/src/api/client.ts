import type {
  BlueprintBatch,
  BoardNode,
  ChapterDetail,
  PreviewResult,
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
  const res = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...init,
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
