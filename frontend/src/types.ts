export interface Concept {
  id: number;
  concept_id: string | null;
  board: string;
  book: string;
  grade: string;
  subject: string;
  chapter_no: string;
  chapter_code: string;
  chapter_title: string;
  topic: string;
  parent_concept: string;
  concept: string;
  concept_description: string;
  mmd_path: string;
  pdf_path: string;
  is_pre_learning: number;
  created_at: string;
}

export interface AnswerOption {
  answer_type: string;
  answer_content: string;
  correct_answer: boolean;
  answer_weightage: number;
}

export interface Question {
  id: number;
  question_label: string | null;
  sheet_kind: string;
  question_category: string;
  cognitive_skills: string;
  question_source: string;
  question_appears_in: string;
  level_of_difficulty: string;
  question: string;
  marks: number;
  answers: AnswerOption[];
  answer_explanation: string;
  display_answer: string;
  rubric: string;
  concept_id: number | null;
  assessment_label: string;
  tagging_notes: string;
  created_at: string;
}

export interface StageDescriptor {
  key: string;
  title: string;
  order: number;
  description: string;
  inputs: string[];
  outputs: string[];
  dependencies: string[];
  requires_keys: string[];
  available: boolean;
}

export interface PipelineRun {
  id: number;
  stage: string;
  mode: string;
  status: string;
  phase: string;
  progress: number;
  detail: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  artifact_path: string;
  started_at: string;
  finished_at: string | null;
  error: string;
}

export interface Stats {
  concepts: number;
  pre_learning_concepts: number;
  questions: number;
  questions_by_sheet: Record<string, number>;
  questions_by_difficulty: Record<string, number>;
  runs: number;
  runs_by_status: Record<string, number>;
}

export interface TagSuggestion {
  concept_id: number | null;
  concept_path: string;
  cognitive_skills: string;
  level_of_difficulty: string;
  confidence: number;
}

export interface Chapter {
  chapter_code: string;
  chapter_title: string;
  subject: string;
  grade: string;
  board: string;
}
