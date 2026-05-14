import type {
  Chapter,
  Concept,
  PipelineRun,
  Question,
  StageDescriptor,
  Stats,
  TagSuggestion,
} from "../types";

const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  base: BASE,

  health: () => http<{ status: string }>("/health"),
  stats: () => http<Stats>("/stats"),

  concepts: (params: Record<string, string> = {}) =>
    http<Concept[]>(`/concepts?${new URLSearchParams(params)}`),
  chapters: () => http<Chapter[]>("/concepts/chapters"),

  questions: (params: Record<string, string> = {}) =>
    http<Question[]>(`/questions?${new URLSearchParams(params)}`),
  createQuestion: (q: Partial<Question>) =>
    http<Question>("/questions", { method: "POST", body: JSON.stringify(q) }),
  applyTag: (id: number) =>
    http<Question>(`/tags/apply/${id}`, { method: "POST" }),

  suggestTag: (text: string) =>
    http<TagSuggestion>("/tags/suggest", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  stages: () => http<StageDescriptor[]>("/pipeline/stages"),
  runStage: (key: string, mode: string, inputs: Record<string, unknown> = {}) =>
    http<PipelineRun>(`/pipeline/stages/${key}/run`, {
      method: "POST",
      body: JSON.stringify({ mode, inputs }),
    }),
  runs: () => http<PipelineRun[]>("/pipeline/runs"),

  exportUrl: () => `${BASE}/export/bulk-upload`,
};
