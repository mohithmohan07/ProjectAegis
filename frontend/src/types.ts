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
}

/**
 * Billing-token usage returned by the backend.
 *
 * Uploaded-file jobs expose a durable cumulative total for that physical
 * source/output across the original attempt and every checkpoint retry.
 * Other endpoints may expose a single-run total. `cached_input_tokens` is a
 * subset of `input_tokens`, not an additional count.
 */
/** Per-(stage, lane) usage attribution for the run console's stage cards.
    Run-scoped: describes the current run only, never persisted history. */
export interface StageUsageRow {
  stage: string;
  lane: string;
  request_count: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  pricing_complete: boolean;
  first_ts: number;
  last_ts: number;
}

export interface OpenAIUsage {
  model: string;
  stages?: StageUsageRow[];
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

export type SemanticDecisionChoice =
  | "expand_existing"
  | "create_new"
  | "select_existing"
  | "accept_recommended"
  | "select_candidate"
  | "replace_source"
  | "consolidate_types"
  | "keep_distinct_types"
  | "custom_instruction";

export interface SemanticDecisionCandidate {
  target_id?: string;
  concept_id: string;
  title: string;
  topic?: string;
  coverage?: string;
  gap?: string;
  action?: string;
  source_block_ids?: string[];
  source_topic_id?: string;
  target_topic_id?: string;
  boundary_relation?: string;
  source_kind?: string;
  source_page?: string | number;
  text_sha256?: string;
  binding_hash?: string;
  [key: string]: unknown;
}

export interface SemanticDecisionEvidence {
  evidence_id?: string;
  label: string;
  text: string;
  page: string;
  [key: string]: unknown;
}

export interface SemanticSourcePatchPreview {
  version: string;
  kind: "canonical_topic_binding";
  target: "working_derived_source";
  verified: boolean;
  raw_source_mutated: false;
  source_contract_hash: string;
  semantic_context_hash: string;
  before_sha256: string;
  after_sha256: string;
  patch_hash: string;
  target_id: string;
  before: string;
  after: string;
  operations: string[];
  [key: string]: unknown;
}

export interface SemanticDecisionItem {
  unit_id: string;
  type_id: string;
  type_title: string;
  qids: string[];
  questions: string[];
  topic: string;
}

export interface SemanticDecisionOption {
  choice: SemanticDecisionChoice;
  label: string;
  recommended: boolean;
  target_concept_id?: string;
  target_id?: string;
}

export type AgentSemanticReviewStatus =
  | "request_started"
  | "resolved"
  | "escalated"
  | "unavailable";

/**
 * Durable record of the single bounded autonomous review that ran before a
 * semantic decision was handed to the user. Its presence is informational:
 * displaying it must never trigger or imply another model request.
 */
export interface AgentSemanticReview {
  status: AgentSemanticReviewStatus;
  resolver_version: string;
  issue_key: string;
  capability_key?: string;
  workspace_hash?: string;
  offered_candidate_count?: number;
  inspected_candidate_count?: number;
  started_at: string;
  completed_at: string;
  reason: string;
  confidence: number;
  evidence_refs: string[];
  choice?: SemanticDecisionChoice | null;
  instruction: string;
  target_id: string;
  target_concept_id: string;
  [key: string]: unknown;
}

/**
 * A persisted semantic choice that needs a human before generation may
 * continue. While this object is present, the backend is checkpointed and no
 * OpenAI request is running.
 */
export interface PendingSemanticDecision {
  decision_id: string;
  kind?: string;
  phase?: string;
  conflict: string;
  diagnosis?: string;
  decision_question?: string;
  item: SemanticDecisionItem;
  candidates: SemanticDecisionCandidate[];
  evidence: SemanticDecisionEvidence[];
  deferred_assignment_unit_ids?: string[];
  options: SemanticDecisionOption[];
  source_patch?: SemanticSourcePatchPreview | null;
  agent_review?: AgentSemanticReview;
  cumulative_usage?: OpenAIUsage;
  // Compatibility with early decision payloads generated before the stable
  // nested `item` contract was introduced.
  item_id?: string;
  qids?: string[];
  question?: string;
  questions?: string[];
  type?: string | Record<string, unknown>;
  topic?: string | Record<string, unknown>;
  reason?: string;
  checkpoint_progress?: number;
  [key: string]: unknown;
}

export interface SemanticDecisionSubmission {
  choice: SemanticDecisionChoice;
  instruction?: string;
  target_concept_id?: string;
  target_id?: string;
}

export interface SemanticDecisionSubmissionResult {
  status: "decision_recorded" | string;
  resume_required: boolean;
  resolved_decision?: Record<string, unknown>;
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
  /**
   * Entries the release manifest folds in are not all downloads: the
   * database-upload entries render as a button that POSTs to
   * `download_url` (Rule G — publication is a separate, explicit,
   * authenticated act). One job stages two releases, so there is one such
   * entry per lane and the lane rides that URL's query. Declared here
   * because the component already relied on all three fields; leaving
   * them off the type is what let a handler ignore `download_url` and
   * publish the wrong lane without the type checker noticing.
   */
  action?: "download" | "post" | string;
  disabled?: boolean;
  /**
   * Why an ENABLED output will be empty when opened — the run's own
   * recorded Pre-lane refusal or verdict, transcribed by the manifest.
   * The download stays live (Rule E); the card explains the contents.
   */
  note?: string;
  requires_confirmation?: boolean;
}

export interface SourceArtifactManifest {
  available: boolean;
  shadow_mode: boolean;
  used_for_generation: boolean;
  schema_version: string;
  compiler_version: string;
  phase?: string;
  consumer_module?: string;
  generation_usage?: {
    mode: string;
    components?: string[];
    raw_mmd_components?: string[];
  };
  phase2_inventory_ready?: boolean;
  source_adjudication?: {
    version?: string;
    status?: "pending" | "verified" | "review_required" | "not_required" | "unavailable" | string;
    packet_count?: number;
    eligible_issue_count?: number;
    verified_repairs?: number;
    remaining_issues?: number;
  };
  source_reconstruction?: {
    version?: string;
    compiler?: string;
    status?: "verified" | "not_used" | "review_required" | string;
    source_origin?: string;
    fallback_reason?: string[];
    failure_reason?: string;
    model?: string;
    page_count?: number;
    batch_count?: number;
    asset_count?: number;
    verified_task_visual_relationships?: number;
  };
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
  awaiting_decision?: boolean;
  pending_decision?: PendingSemanticDecision | null;
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
  picture?: string;
  hd?: string;
}

export interface AuthSession {
  authenticated: boolean;
  user: AuthUser | null;
}

export interface ResumableCheckpoint {
  id: number;
  module: string;
  learning_kind: string;
  filename: string;
  status: string;
  checkpoint_available: boolean;
  checkpoint_stage?: string;
  checkpoint_saved_at?: string;
  checkpoint_progress?: number;
  checkpoint_target_identity?: Record<string, string>;
  generation_running?: boolean;
  created_at: string;
}

export interface ResumableCheckpoints {
  items: ResumableCheckpoint[];
  total: number;
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

/** One post-run reviewer instruction and the edit Aegis made from it. */
export interface ConceptRevisionChange {
  concept_id: number;
  field: string;
  before: string;
  after: string;
  reason: string;
}

export interface ConceptRevision {
  id: number;
  job_id: number;
  round_number: number;
  instruction: string;
  status: "pending" | "applied" | "no_change" | "failed" | string;
  change_summary: string;
  changes: ConceptRevisionChange[];
  change_count: number;
  flagged_placements: { level: string; message: string }[];
  model: string;
  error: string;
  created_at: string;
  completed_at: string;
}

export interface ConceptRevisionList {
  job_id: number;
  revisions: ConceptRevision[];
}

/* ---- Release review (step 9: the review/edit surface) ------------------ */

export type ReleaseReviewLane = "post" | "pre";

/** One released concept row, addressed by `record_index` for edits. */
export interface ReleaseReviewConcept {
  record_index: number;
  parent_concept: string;
  concept_title: string;
  /** House format: " // "-joined sections, later ones "Label:"-prefixed. */
  concept_details: string;
  keywords: string;
  review_flags: string[];
  release_status: string;
  release_errors: string[];
}

export interface ReleaseReviewTopic {
  topic: string;
  concepts: ReleaseReviewConcept[];
}

export interface ReleaseReviewIssue {
  code: string;
  severity: string;
  message: string;
}

export interface ReleaseReviewVersion {
  version: number;
  staged_release_uid: string;
  origin: "staged" | "manual_edit" | "instruction";
  instruction: string | null;
  change_count: number;
  created_at: string;
}

export interface ReleaseReviewSummary {
  row_count: number;
  issue_count: number;
  error_count: number;
  warning_count: number;
  database_uploaded: boolean;
}

export interface ReleaseReviewView {
  job_id: number;
  lane: ReleaseReviewLane;
  staged_version: number;
  staged_release_uid: string;
  state: "ready" | "ready_with_flags" | "diagnostic_release";
  summary: ReleaseReviewSummary;
  topics: ReleaseReviewTopic[];
  issues: ReleaseReviewIssue[];
  versions: ReleaseReviewVersion[];
}

export type ReleaseReviewEditField =
  | "topic"
  | "parent_concept"
  | "concept_title"
  | "concept_details"
  | "keywords";

/** One field-level change; `before` lets the server detect stale edits. */
export interface ReleaseReviewEdit {
  record_index: number;
  field: ReleaseReviewEditField;
  before: string;
  after: string;
}

export interface ReleaseManualEditBody {
  lane: ReleaseReviewLane;
  staged_release_uid: string;
  edits: ReleaseReviewEdit[];
}

export interface ReleaseInstructionBody {
  lane: ReleaseReviewLane;
  staged_release_uid: string;
  instruction: string;
}
