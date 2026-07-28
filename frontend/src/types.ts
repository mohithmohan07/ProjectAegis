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
 * Billing-token usage returned by the backend.
 *
 * Uploaded-file jobs expose a durable cumulative total for that physical
 * source/output across the original attempt and every checkpoint retry.
 * Other endpoints may expose a single-run total. `cached_input_tokens` is a
 * subset of `input_tokens`, not an additional count.
 */
export interface OpenAIUsage {
  model: string;
  models?: Array<{
    model: string;
    request_count: number;
    input_tokens: number;
    cached_input_tokens: number;
    cache_write_tokens?: number;
    uncached_input_tokens: number;
    output_tokens: number;
    reasoning_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number | null;
  }>;
  request_count: number;
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens?: number;
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

export interface SourceArtifactFile {
  kind: "raw_mmd" | "canonical_json" | "aegis_mmd" | "report" | string;
  label: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  download_url: string;
}

export interface SourceArtifactManifest {
  available: boolean;
  shadow_mode: true;
  used_for_generation: false;
  schema_version: string;
  compiler_version: string;
  status: "passed" | "passed_with_warnings" | "failed" | "unavailable" | string;
  ready_for_future_cutover: boolean;
  source_sha256: string;
  manifest_url: string;
  summary: {
    source_chars?: number;
    sections?: number;
    blocks?: number;
    figures?: number;
    images?: number;
    math_spans?: number;
    tasks?: number;
    errors?: number;
    warnings?: number;
    [key: string]: number | undefined;
  };
  files: SourceArtifactFile[];
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
  source_artifacts?: SourceArtifactManifest;
  checkpoint_available?: boolean;
  checkpoint_stage?: string;
  checkpoint_saved_at?: string;
  checkpoint_progress?: number;
  checkpoint_target_identity?: Record<string, string>;
  generation_running?: boolean;
  generation_log?: Array<{
    type: string;
    level?: string;
    message?: string;
    label?: string;
    value?: number;
    ts?: number;
  }>;
  created_at: string;
  openai_usage?: OpenAIUsage;
}

export interface AuthConfig {
  mode: "local" | "google";
  google_client_id: string;
  allowed_google_domain: string;
  csrf_token: string;
  drive_checkpoint_backup?: {
    enabled: boolean;
    configured: boolean;
    auth_mode: string;
    notice: string;
    state?: string;
    verified?: boolean;
  };
}

export interface AuthUser {
  sub: string;
  email: string;
  name: string;
  picture: string;
  hd: string;
}
