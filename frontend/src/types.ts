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

/**
 * `GET /directory/resolve-chapter`: the chapter a saved checkpoint targets.
 *
 * A checkpoint stores the chapter's RAW subject (`History` for a CBSE
 * social-science chapter) while the directory presents that chapter under
 * `Social Science`, so the two sides cannot be compared as plain strings.
 * The server does the lookup across that fold and answers with the values
 * the dropdowns hold.
 *
 * The call always answers HTTP 200 — an identity that does not resolve
 * carries `reason` (a full sentence naming the level that failed) and a null
 * chapter, and a refusal omits the placement fields entirely, which is why
 * they are optional here.
 */
export interface ResolvedSavedChapter {
  resolved: boolean;
  /** "" when resolved; otherwise the sentence to show the reviewer verbatim. */
  reason: string;
  chapter: {
    id: number;
    chapter_code: string;
    chapter_title: string;
    chapter_display_name: string;
  } | null;
  board?: string;
  grade?: string;
  /** The subject the DIRECTORY shows, e.g. "Social Science". */
  subject?: string;
  unit?: string;
  /** True when the stored subject differs from the directory's. */
  subject_folded?: boolean;
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
    Cumulative: merged across run segments (the parse run and every
    generation attempt), persisted with the summary. */
export interface InrUsage {
  /** API-converted amounts use the exchange rate recorded for each request. */
  estimated_cost_inr?: number | null;
  known_usage_estimated_cost_inr?: number | null;
  inr_conversion_complete?: boolean;
  usd_to_inr_rate?: string | null;
  usd_to_inr_as_of?: string | null;
  usd_to_inr_source?: string | null;
  usd_to_inr_kind?: string | null;
}

export interface ProviderRequestUsage extends InrUsage {
  attempt_id: string;
  model?: string;
  requested_model?: string;
  actual_model?: string | null;
  stage?: string;
  lane?: string;
  status?: string;
  outcome?: string;
  usage_reported?: boolean;
  total_tokens?: number;
  estimated_cost_usd?: number | null;
  service_started_at?: number | null;
  service_ended_at?: number | null;
}

export interface StageUsageRow extends InrUsage {
  stage: string;
  lane: string;
  request_count: number;
  /** Physical provider requests, including attempts without usage receipts. */
  provider_request_count?: number;
  attempt_count?: number;
  usage_complete?: boolean;
  attempt_coverage_complete?: boolean;
  missing_usage_response_count?: number;
  /** Requests still running; their eventual tokens/cost are not yet recorded. */
  pending_request_count?: number;
  /** Finished provider requests whose usage could not be recovered. */
  unresolved_usage_request_count?: number;
  input_tokens: number;
  cached_input_tokens?: number;
  cache_write_tokens?: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  /** Recorded priced usage only, retained even when the full total is unknown. */
  known_usage_estimated_cost_usd?: number;
  pricing_complete: boolean;
  first_ts: number;
  last_ts: number;
  /** Wall-clock seconds this stage ran, cumulative across segments. */
  elapsed_seconds?: number;
}

/** Cumulative usage and recorded charges for one provider family.  The
 * backend keeps this alongside the top-level ledger so the UI can show a
 * provider split without re-pricing historical receipts in the browser. */
export interface ProviderUsageSummary extends InrUsage {
  /** Stable provider key (openai, gemini, or unknown). */
  provider?: "openai" | "gemini" | "unknown";
  model?: string;
  request_count?: number;
  provider_request_count?: number;
  attempt_count?: number;
  usage_complete?: boolean;
  attempt_coverage_complete?: boolean;
  missing_usage_response_count?: number;
  pending_request_count?: number;
  unresolved_usage_request_count?: number;
  untracked_response_count?: number;
  input_tokens?: number;
  cached_input_tokens?: number;
  cache_write_tokens?: number;
  uncached_input_tokens?: number;
  output_tokens?: number;
  reasoning_tokens?: number;
  total_tokens?: number;
  estimated_cost_usd?: number | null;
  known_usage_estimated_cost_usd?: number;
  pricing_complete?: boolean;
  /** Provider rows may expose stage/request detail in future versions. */
  stages?: StageUsageRow[];
}

export interface OpenAIUsage extends InrUsage {
  model: string;
  stages?: StageUsageRow[];
  models?: Array<InrUsage & {
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
  request_attempts?: ProviderRequestUsage[];
  latest_request?: ProviderRequestUsage | null;
  /** Durable provider-family totals (OpenAI/GPT, Gemini, and unknown). */
  providers?: ProviderUsageSummary[];
  /** request_count remains the legacy count of usage-bearing responses. */
  provider_request_count?: number;
  attempt_count?: number;
  usage_complete?: boolean;
  attempt_coverage_complete?: boolean;
  missing_usage_response_count?: number;
  pending_request_count?: number;
  unresolved_usage_request_count?: number;
  untracked_response_count?: number;
  mechanical_wall_seconds?: number;
  mechanical_thread_cpu_seconds?: number;
  mechanical_span_count?: number;
  mechanical_cpu_complete?: boolean;
  input_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens?: number;
  uncached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  /** Recorded priced usage only; excludes pending and unresolved charges. */
  known_usage_estimated_cost_usd?: number;
  currency?: "USD" | string;
  pricing_source?: string;
  pricing_as_of?: string;
  pricing_complete?: boolean;
  /** Cumulative wall-clock seconds across the parse run and every
      generation attempt. */
  elapsed_seconds?: number;
  /** Active provider/pipeline processing time, excluding human review wait. */
  active_elapsed_seconds?: number;
  /** Time spent waiting for the reviewer at the Concept → Master boundary. */
  review_wait_seconds?: number;
  /** Total wall time from the first Concept stage through completion. */
  wall_elapsed_seconds?: number;
}

/** Durable same-run lifecycle and timing state returned with an upload job. */
export interface DurableRunState {
  schema_version: number;
  run_id: string;
  status: "processing" | "review" | "master" | "completed" | "failed";
  stage: string;
  progress: number;
  started_at?: string;
  started_at_epoch?: number;
  active_started_at_epoch?: number | null;
  active_elapsed_seconds: number;
  review_started_at_epoch?: number | null;
  review_wait_seconds: number;
  wall_elapsed_seconds: number;
  last_updated_at?: string;
  finished_at?: string;
  finished_at_epoch?: number | null;
  stage_history?: Array<{
    stage: string;
    progress: number;
    started_at: string;
    ended_at: string;
    active_elapsed_seconds: number;
  }>;
  progress_events?: Array<{
    at: string;
    value: number;
    label: string;
    stage: string;
  }>;
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
  /** Why an output is unavailable. Master cards use this durable reason to
   * explain a failed lane and to retain the evidence after a retry. */
  disabled_reason?: string;
  /**
   * Why an ENABLED output will be empty when opened — the run's own
   * recorded Pre-lane refusal or verdict, transcribed by the manifest.
   * The download stays live (Rule E); the card explains the contents.
   */
  note?: string;
  requires_confirmation?: boolean;
  /** Publication entries (`database_upload` / `pre_database_upload`) carry
   * the staged release's recorded state; `disabled` there means the lane's
   * Concept file is already uploaded, unless a `disabled_reason` explains
   * that the lane was never staged. */
  release_state?: string;
  structural_defects?: unknown;
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

/** Durable state for the Concept-first workflow.  Legacy jobs omit this
 * projection and continue to use the released-output surface below. */
export type ConceptReviewStatus =
  | "pending_review"
  | "reviewed"
  | "master_building"
  | "master_ready"
  | "master_failed"
  | "published";

export interface CorrectedConceptInput {
  lane: "post" | "pre";
  filename?: string;
  uploaded_at?: string;
  status?: string;
  accepted?: boolean;
  [key: string]: unknown;
}

/** Counts the backend records when one lane's Master is published. */
export interface MasterReviewDatabaseReceipt {
  groups_created?: number;
  questions_created?: number;
  labels_reissued?: number;
  [key: string]: unknown;
}

/** One reviewed cell edit, as `master_review.py` records it: one entry per
 * changed cell, each naming the question it belongs to. */
export interface MasterReviewFieldEdit {
  question_label?: string;
  field?: string;
  before?: unknown;
  after?: unknown;
  [key: string]: unknown;
}

/** One question the reviewer dropped from (or added to) the Master file. */
export interface MasterReviewQuestionChange {
  question_label?: string;
  candidate_id?: string;
  sheet_kind?: string;
  group_key?: string;
  concept_key?: string;
  [key: string]: unknown;
}

/** The backend emits these as lists of records; a bare count is tolerated so
 * an older or summarising payload still renders. */
export type MasterReviewEdits = MasterReviewFieldEdit[] | number;
export type MasterReviewQuestionChanges = MasterReviewQuestionChange[] | number;

/** The CMS workbook half of a Master publication. `status` is `published` or
 * `queued`; a queued append kept the committed database write and names its
 * `queued_reason`, and the publish act converges when it is repeated. */
export interface MasterReviewCmsReceipt {
  status?: string;
  queued_reason?: string;
  path?: string;
  [key: string]: unknown;
}

export interface MasterReviewPublication {
  uploaded_at?: string;
  database?: MasterReviewDatabaseReceipt | null;
  cms_workbook?: MasterReviewCmsReceipt | null;
  /** Carried from a publish response (`published` or `queued`) so a local
   * receipt knows its own state before the marker refreshes. */
  publication_status?: string;
  [key: string]: unknown;
}

/** Step 03 state for one lane: the accepted reviewed Master file (if any)
 * and, once published, the database/CMS receipt. */
export interface MasterReviewLaneState {
  filename?: string;
  sha256?: string;
  uploaded_at?: string;
  release_id?: number;
  release_uid?: string;
  version?: number;
  changed_fields?: MasterReviewEdits;
  omitted?: MasterReviewQuestionChanges;
  added?: MasterReviewQuestionChanges;
  readiness?: string;
  issues?: string[];
  status?: string;
  published?: MasterReviewPublication | null;
  [key: string]: unknown;
}

export interface ReviewWorkflow {
  status?: ConceptReviewStatus;
  concepts_ready?: boolean;
  masters_ready?: boolean;
  checkpoint_progress?: number;
  progress?: number;
  corrected_inputs?: Partial<Record<"post" | "pre", CorrectedConceptInput | boolean | string>>;
  concept_files?: Partial<Record<"post" | "pre", SourceArtifactFile>>;
  master_review?: Partial<Record<"post" | "pre", MasterReviewLaneState>>;
  reviewed_at?: string;
  master_started_at?: string;
  master_completed_at?: string;
  [key: string]: unknown;
}

/** Acknowledgement for a reviewed Master file (Step 03 upload). */
export interface MasterReviewSubmitResult {
  lane: "post" | "pre";
  filename?: string;
  input_sha256?: string;
  release_id?: number;
  release_uid?: string;
  version?: number;
  round_recorded?: boolean;
  changed_fields?: MasterReviewEdits;
  omitted_questions?: MasterReviewQuestionChanges;
  added_questions?: MasterReviewQuestionChanges;
  readiness?: string;
  issues?: string[];
  master_review?: MasterReviewLaneState;
  review_workflow?: ReviewWorkflow;
  [key: string]: unknown;
}

/** Receipt for one lane's Master publication to the database and CMS. */
export interface MasterReviewPublishResult {
  lane: "post" | "pre";
  release_id?: number;
  release_uid?: string;
  version?: number;
  database?: MasterReviewDatabaseReceipt | null;
  cms_workbook?: MasterReviewCmsReceipt | null;
  /** `published` once the CMS workbook append landed; `queued` when only the
   * database half is committed and the act must be repeated. */
  publication_status?: string;
  master_review?: MasterReviewLaneState;
  review_workflow?: ReviewWorkflow;
  [key: string]: unknown;
}

export interface UploadJob {
  id: number;
  module: string;
  upload_type: string;
  textbook_mode: string;
  learning_kind: string;
  source_book?: string;
  chapter_duration_minutes?: number;
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
  generation_recovery?: GenerationRecovery;
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
  run_id?: string;
  run_state?: DurableRunState;
  /** New Concept-first workflow state; absent on historical full-run jobs. */
  review_workflow?: ReviewWorkflow | null;
}

export interface GenerationRecovery {
  error?: string;
  message?: string;
  resume_allowed: boolean;
  recovery_action?: string;
  recovery?: string;
  /** Compatibility text present only for resumable failures. */
  resume?: string;
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
  /** Additive projections used to distinguish a Master continuation from
   * replay of the earlier Concept segment when attaching after refresh. */
  review_workflow?: ReviewWorkflow | null;
  run_id?: string;
  run_state?: DurableRunState;
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

/* ======================================================================
   Chapter batch console (Q53)
   ----------------------------------------------------------------------
   The frozen contract's Appendix A, declared verbatim. The backend emits
   exactly these keys; a rename on either side turns every console call
   into a 404 or a blank cell, which is why both sides code against the
   same block. See docs/chapter-batch-console-contract.md.
   ====================================================================== */

export type ChapterBatchState =
  | "no_source" | "source_staged" | "step01_queued" | "step01_running"
  | "recovering" | "concept_review" | "reviewed" | "step02_queued"
  | "step02_running" | "master_failed" | "master_review" | "publish_queued"
  | "publish_running" | "partly_published" | "published" | "blocked"
  | "failed" | "dead" | "cancelled" | "legacy";

export type ChapterBatchStep = "step01" | "step02" | "publish";

export interface ChapterBatchLane {
  lane: string;                       // "post" | "pre"
  available: boolean;
  concept_reviewed: boolean;
  concept_reviewed_filename: string;
  concept: "published" | "available" | "unavailable";
  concept_reason: string;
  master: "published" | "queued" | "ready" | "none";
  master_reason: string;
  master_version: number;
}

export interface ChapterBatchQueue {
  task_id: number | null;
  kind: ChapterBatchStep | null;
  state: "queued" | "leased" | "blocked" | "done" | "failed" | "cancelled" | null;
  position: number | null;            // 1-based place in the queue, null unless queued
  attempt: number;
  max_attempts: number;
  blocked_kind: string;
  failure_code: string;
  last_error: string;
  enqueued_by_email: string;
  enqueued_at: string | null;
  started_at: string | null;
  lease_expired: boolean;             // leased but the lease ran out -> "recovering"
}

export interface ChapterBatchPendingDecision {
  decision_id: string;
  kind: string;
  question: string;
  companions: number;
}

export interface ChapterBatchCan {
  step01: boolean; step02: boolean; publish: boolean;
  cancel: boolean; retry: boolean;
  upload_source: boolean; upload_concept: boolean; upload_master: boolean;
}

export interface ChapterBatchRow {
  chapter_id: number;
  chapter_code: string;
  chapter_title: string;
  chapter_display_name: string;
  board: string; grade: string; subject: string; unit: string;
  job_id: number | null;
  source_filename: string;
  source_book: string;
  staged_by_email: string;
  source_staged_at: string | null;
  state: ChapterBatchState;
  state_label: string;                // server-supplied; the client never invents one
  stage: string;                      // live stage name, "" when idle
  progress: number;                   // 0..1
  workflow_status: string;            // the Concept-review marker status, or ""
  lanes: ChapterBatchLane[];
  blocked_kind: string;
  blocked_reason: string;
  error_message: string;
  pending_decision: ChapterBatchPendingDecision | null;
  can: ChapterBatchCan;               // the server is the authority
  queue: ChapterBatchQueue;
  last_actor_email: string;
  last_actor_act: string;
  last_actor_at: string | null;
  updated_at: string | null;
}

export interface ChapterBatchFacets {
  boards: string[]; grades: string[]; subjects: string[];
  triples: Array<{ board: string; grade: string; subject: string }>;
}

export interface ChapterBatchQueueSummary {
  running: number; queued: number; blocked: number;
  capacity: number;                   // max concurrent generation runs
  worker_alive: boolean;
}

export interface ChapterBatchPage {
  items: ChapterBatchRow[];
  page: number; page_size: number; total: number; total_pages: number;
  facets: ChapterBatchFacets;
  states: Array<{ value: ChapterBatchState; label: string; tone: string }>;
  queue: ChapterBatchQueueSummary;
  server_time: string;
}

export interface ChapterBatchPushOutcome {
  chapter_id: number;
  verdict: "queued" | "already_queued" | "already_running" | "refused";
  reason_code: string;                // "" when queued
  reason: string;                     // human sentence, "" when queued
  task_id: number | null;
  position: number | null;
  /**
   * Freshly projected, for an optimistic patch. The server projects it
   * after the act, so a chapter that no longer exists (an `unknown_chapter`
   * refusal, a row left over from a pruned syllabus) carries `null` here.
   */
  row: ChapterBatchRow | null;
}

export interface ChapterBatchPushResult {
  /**
   * Appendix A declares a `ChapterBatchStep` here, and `/push` sends one.
   * `/cancel` and `/retry` share this envelope and answer with an EMPTY
   * step (`api/chapter_batches.py::_bulk`), so the declared type is widened
   * rather than lying to the receipt, which otherwise prints a sentence
   * beginning with a bare colon.
   */
  step: ChapterBatchStep | "";
  push_group_id: string;
  results: ChapterBatchPushOutcome[];
}

export interface ChapterBatchDetail {
  row: ChapterBatchRow;
  job: UploadJob | null;              // the full job, drawer only
}
