export interface ChapterRef {
  id: number;
  chapter_code: string;
  chapter_title: string;
  chapter_display_name: string;
  topic_count: number;
  concept_count: number;
}

export interface Unit {
  unit: string;
  chapters: ChapterRef[];
}
export interface SubjectNode {
  subject: string;
  units: Unit[];
}
export interface GradeNode {
  grade: string;
  subjects: SubjectNode[];
}
export interface BoardNode {
  board: string;
  grades: GradeNode[];
}

export interface ConceptRef {
  id: number;
  concept_title: string;
  concept_display_name: string;
  sources?: string;
  group_count: number;
  question_count: number;
}
export interface TopicNode {
  id: number;
  topic_title: string;
  topic_display_name: string;
  pre_post_learning: string;
  concepts: ConceptRef[];
}
export interface ChapterDetail {
  id: number;
  chapter_code: string;
  chapter_title: string;
  chapter_display_name: string;
  board: string;
  grade: string;
  subject: string;
  unit: string;
  topics: TopicNode[];
}

export interface Vocab {
  boards: string[];
  grades: string[];
  question_types: string[];
  cognitive_skills: string[];
  difficulty_levels: string[];
  question_categories: Record<string, string[]>;
  group_types: string[];
  upload_types: string[];
  book_sources: string[];
  appears_in: string[];
}

export interface Stats {
  chapters: number;
  topics: number;
  concepts: number;
  groups: number;
  questions: number;
  questions_by_sheet: Record<string, number>;
  sessions: number;
  upload_jobs: number;
  openai_live: boolean;
  mathpix_live: boolean;
}

/**
 * Billing-token usage returned by the backend for one generation run or file.
 * `cached_input_tokens` is a subset of `input_tokens`, not an additional count.
 */
export interface OpenAIUsage {
  model: string;
  models?: Array<{
    model: string;
    request_count: number;
    input_tokens: number;
    cached_input_tokens: number;
    uncached_input_tokens: number;
    output_tokens: number;
    reasoning_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number | null;
  }>;
  request_count: number;
  input_tokens: number;
  cached_input_tokens: number;
  uncached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  currency?: "USD" | string;
  pricing_source?: string;
  pricing_as_of?: string;
  pricing_complete?: boolean;
}

export interface BlueprintBatch {
  id: number;
  cognitive_skills: string[];
  difficulty_levels: string[];
  categories: string[];
  question_type: string;
  num_questions: number;
  appears_in?: string[];
}

export interface Session {
  id: number;
  source: string;
  scope_type: string;
  scope_ids: number[];
  status: string;
  generated_question_ids: number[];
  batches: BlueprintBatch[];
  created_at: string;
}

export interface UploadJob {
  id: number;
  module: string;
  upload_type: string;
  textbook_mode: string;
  learning_kind: string;
  source_book?: string;
  filename: string;
  mmd_text: string;
  deposit_scope_type: string;
  deposit_scope_ids: number[];
  status: string;
  result_ids: number[];
  detail: string;
  created_at: string;
  openai_usage?: OpenAIUsage;
}

export interface Question {
  id: number;
  group_id: number;
  sheet_kind: string;
  question_label: string;
  question_category: string;
  cognitive_skills: string;
  question_source: string;
  level_of_difficulty: string;
  question: string;
  marks: number;
  math_keyboard: string;
  display_answer: string;
  answer_explanation: string;
  answers: Record<string, unknown>[];
  sub_questions: Record<string, unknown>[];
  origin: string;
  created_at: string;
}

export type ScopeType = "chapter" | "topic" | "concept";
export interface Scope {
  type: ScopeType;
  ids: number[];
  label: string;
}

export interface TagResult {
  status: string;
  reason?: string;
  question_label?: string;
  concept_title?: string;
  chapter_title?: string;
  topic_title?: string;
}

export type Outcome = "ADD" | "TAG" | "SKIP";
export interface PreviewRow {
  kind: string;
  outcome: Outcome;
  identity: string;
  sheet?: string;
  placement: Record<string, string>;
}
export interface PreviewResult {
  rows: PreviewRow[];
  summary: Record<string, number>;
  workbook: string;
}

export interface WorkbookResult {
  output_pdf: string;
  build_log: string;
  valid: boolean;
  issues: string[];
  mode: "dry" | "live";
  meta: Record<string, string>;
  log: string;
  openai_usage?: OpenAIUsage;
}

export interface PromptInfo {
  key: string;
  label: string;
  category: string;
  description: string;
  variables: string[];
  default: string;
  current: string;
  overridden: boolean;
}

export interface WorkbookEntry {
  class_folder: string;
  subject: string;
  name: string;
  rel: string;
  size: number;
  has_log: boolean;
  openai_usage?: OpenAIUsage;
}
