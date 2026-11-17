import type {
  AnalysisRun,
  Block,
  Finding,
  Lens,
  Project,
  SourceDocument,
} from "../types";
export type { AnalysisRun, Block, Finding, Lens, Project, SourceDocument };
export type Item = Record<string, unknown>;
export type Drawer =
  | "import"
  | "analyze"
  | "models"
  | "jobs"
  | "reports"
  | "learning"
  | "filters"
  | "review-queue"
  | "help"
  | null;
export type Scope = {
  run_id: string;
  q: string;
  date_basis: "publication" | "ingestion";
  from_date: string;
  to_date: string;
  lens_ids: string[];
  criterion_ids: string[];
  authors: string[];
  document_types: string[];
  batch_ids: string[];
  document_ids: string[];
  review_status: string;
  speech_roles: string[];
  stance: string;
  results: string[];
  metric: string;
  bin: string;
  duplicate_policy: "sources" | "exact_content";
};
export const EMPTY_SCOPE: Scope = {
  run_id: "",
  q: "",
  date_basis: "publication",
  from_date: "",
  to_date: "",
  lens_ids: [],
  criterion_ids: [],
  authors: [],
  document_types: [],
  batch_ids: [],
  document_ids: [],
  review_status: "",
  speech_roles: [],
  stance: "",
  results: [],
  metric: "indicators",
  bin: "month",
  duplicate_policy: "exact_content",
};
export type Page<T> = { items: T[]; total: number; next_cursor: string | null };
export type Observation = Finding & {
  criterion_id?: string;
  unit_id?: string;
  detail_supplied?: boolean;
  origin_labels?: Item;
  evidence_selection_origin?: string;
  explanation_origin?: string;
  result?: string;
  hypothetical?: boolean;
  proposition?: string;
  speaker_stance?: string;
  document_stance?: string;
  criterion_kind?: string;
  speech_role?: string;
  methodology_version?: string;
  original?: Item;
  corrections?: Item;
  review_event?: Item;
};
export type CaseSelection = {
  assessment_id?: string;
  document_id: string;
  unit_id?: string;
};
export type CasebookCase = CaseSelection & {
  id: string;
  kind: "assessment" | "unresolved";
  result?: string | null;
  criterion_kind?: string;
  editorial_note?: string;
  selection_rationale?: string;
  ordinal?: number;
  source_snapshot?: Item;
  assessment_snapshot?: Item;
  unit_snapshot?: Item;
  frozen_snapshot?: Item;
  block_id?: string;
  framework_condition?: Item;
  review_status?: string;
};
export type Casebook = {
  id: string;
  project_id: string;
  query_id: string;
  run_id: string;
  scope_hash: string;
  snapshot_version: string;
  title: string;
  selection_rationale: string;
  publication_type: string;
  revision: number;
  cases: CasebookCase[];
};
export type Metrics = {
  documents: number;
  total_words: number;
  total_pages: number;
  findings: number;
  indicators: number;
  reported_indicators: number;
  endorsed_indicators: number;
  uncertain: number;
  reviewed: number;
  coverage: number;
  coverage_detail: Item;
  analysis_kind: string;
  legacy_findings: number;
  not_assessed_lenses?: string[];
  author_analysis?: Item;
  cost_usd: number;
};
export type Facet = {
  name: string;
  count: number;
  id?: string;
  value?: string;
  label?: string;
};
export type Facets = {
  authors: Facet[];
  document_types: Facet[];
  lenses: Facet[];
  criteria: Facet[];
  speech_roles: Facet[];
  review_states: Facet[];
  batches: Facet[];
};
export type TimePoint = {
  date: string;
  documents: number;
  findings: number;
  indicators: number;
  reported_indicators: number;
  endorsed_indicators: number;
  unknown: number;
  coverage: number;
  rate: number | null;
  lower?: number | null;
  upper?: number | null;
  spike_candidate?: boolean;
  eligible_documents?: number;
  covered_documents?: number;
};
export type MatrixCell = {
  author: string;
  lens_id: string;
  criterion_id: string;
  criterion_kind?: string;
  count: number;
  documents: number;
  coverage: number;
  coverage_basis?: string;
  author_grouping?: string;
};
export type QueryResult = {
  query_id: string;
  scope_hash: string;
  snapshot_version: string;
  scope: Scope;
  documents: Page<SourceDocument>;
  findings: Page<Observation>;
  metrics: Metrics;
  facets: Facets;
  timeline: TimePoint[];
  matrix: MatrixCell[];
  run: AnalysisRun | null;
  notice?: string;
};
export type Context = {
  document: SourceDocument;
  blocks: Block[];
  finding?: Observation;
  unit?: Item;
  unit_id?: string;
  total_blocks?: number;
  start?: number;
  next_cursor?: number | null;
};
export type ModelProfile = {
  id: string;
  name?: string;
  provider?: string;
  model?: string;
  description?: string;
  recommended?: boolean;
  available?: boolean;
  memory?: string;
  license?: string;
  [key: string]: unknown;
};
export type Notice = { text: string; error?: boolean } | null;
export const EMPTY_METRICS: Metrics = {
  documents: 0,
  total_words: 0,
  total_pages: 0,
  findings: 0,
  indicators: 0,
  reported_indicators: 0,
  endorsed_indicators: 0,
  uncertain: 0,
  reviewed: 0,
  coverage: 0,
  coverage_detail: {},
  analysis_kind: "",
  legacy_findings: 0,
  cost_usd: 0,
};
export const EMPTY_FACETS: Facets = {
  authors: [],
  document_types: [],
  lenses: [],
  criteria: [],
  speech_roles: [],
  review_states: [],
  batches: [],
};
