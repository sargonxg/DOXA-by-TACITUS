export const criteria = [
  'evaluative_framing', 'collective_blame', 'identity_stereotyping',
  'identity_derogation_exclusion', 'group_conspiracy', 'evidential_overclaim',
  'material_omission', 'comparative_double_standard',
] as const;
export type Criterion = typeof criteria[number];

export interface Span { segment_id: string; start: number; end: number; quote: string; role: string }
export interface Segment { segment_id: string; text: string; provenance?: string }
export interface Case {
  case_id: string; document_id?: string; thread_id?: string; family_id?: string;
  text: string; context_segments?: Segment[]; focal_speaker?: unknown; source_record?: unknown;
  explicit_target_context?: string | string[]; topic_tags?: string[]; source_origin?: string; split?: string;
}
export interface Judgment {
  judgment_id: string; case_id: string; criterion_id: Criterion; reference_decision: string;
  evidence?: Span[]; counterevidence?: Span[]; rationale?: string; missing_context?: string[];
  annotation_status: string; source_origin?: string; human_review_count?: number; framework_id?: string;
  speaker_stance?: string; recommended_action?: string; review_priority_reference?: string;
}
export interface Revision {
  revision_id: string; case_id?: string; original_text: string; revised_text: string;
  changed_mechanism?: string; preserved_meaning?: string | string[]; rationale?: string;
}
export interface Bundle {
  cases: Case[]; judgments: Judgment[]; revisions: Revision[]; pairs?: unknown[];
  metrics?: unknown; trainingSupport?: unknown; localHashes?: Record<string,string>;
  revisionsAtHub?: Record<string,string>; resources?: Record<string,string>;
}
export interface AssessmentCriterion {
  criterion_id: string;
  prediction_status: string;
  suggested_decision?: string | null;
  scores?: Record<string,number> | null;
  suggested_review_priority?: string | null;
  recommendations?: string[];
  evidence_status?: string;
  eligible?: boolean;
  abstention_reason?: string;
  reason?: string;
}
export interface Assessment {
  criteria: AssessmentCriterion[];
  model_revision?: string;
  training_data_revision?: string;
  [key: string]: unknown;
}
export interface RecordInput {
  record_id: string; document_id: string; thread_id: string;
  text: string; context: string; source_reference: string;
  skip_reason?: string;
}
export type Review = 'accept' | 'dismiss' | 'revise' | 'request_context' | 'escalate';
export interface Processed {
  input: RecordInput; group: string; duplicate_of?: string; repeated_quotes: string[];
  assessment?: Assessment; skip_reason?: string;
  reviews: Record<string, { decision: Review; note: string; proposed_edit: string }>;
}
export type WorkerCommand =
  | { kind: 'analyze'; text: string; context: string; modelUrl: string }
  | { kind: 'corpus'; inputs: RecordInput[]; modelUrl: string }
  | { kind: 'cancel' };
export type WorkerReport =
  | { kind: 'loaded' }
  | { kind: 'analysis'; packet: Assessment }
  | { kind: 'progress'; result: Processed; done: number; total: number }
  | { kind: 'complete'; canceled: boolean }
  | { kind: 'error'; message: string };
