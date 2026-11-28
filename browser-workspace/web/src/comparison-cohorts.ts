import type { CriterionCounts } from './document-audit';
import { countSummary, poolCounts } from './report-analysis.ts';

export const comparisonFields = ['event', 'topic', 'genre', 'period'] as const;
export type ComparisonField = typeof comparisonFields[number];
export type ComparisonMetadata = Partial<Record<ComparisonField, string>>;

export type ComparisonCoverage = {
  screened: number;
  eligible: number;
  pages: number;
  emptyPages: number;
  extractionErrors: number;
};

export type ComparisonDocument = {
  id: string;
  label: string;
  metadata: ComparisonMetadata;
  criteria: Record<string, CriterionCounts>;
  modelRevision?: string;
  complete?: boolean;
  coverage?: ComparisonCoverage;
};

export type ComparisonRate = {
  counts: CriterionCounts;
  assessed: number;
  resolved: number;
  unresolved: number;
  flaggedAmongAssessed: number | null;
  flaggedAmongResolved: number | null;
  resolutionCoverage: number | null;
};

export type ComparisonGroup = {
  key: string;
  metadata: ComparisonMetadata;
  documentIds: string[];
  documents: Array<{
    id: string;
    label: string;
    criteria: Record<string, ComparisonRate>;
    complete: boolean | null;
    coverage: ComparisonCoverage | null;
    modelRevision: string | null;
  }>;
  criteria: Record<string, ComparisonRate & { missingDocumentIds: string[] }>;
  coverage: ComparisonCoverage & {
    knownDocumentIds: string[];
    unknownDocumentIds: string[];
    completeDocumentIds: string[];
    partialDocumentIds: string[];
    unknownCompletionDocumentIds: string[];
  };
  modelRevisions: string[];
  missingModelRevisionDocumentIds: string[];
  cautions: string[];
};

export type UnmatchedDocument = {
  id: string;
  label: string;
  reason: 'missing_metadata' | 'no_matching_peer';
  missingFields: ComparisonField[];
};

const countKeys = ['present', 'absent', 'uncertain', 'abstained', 'unsupported'] as const;
const missingLabel = /^(?:unknown|n\/?a|not provided|unassigned|unspecified|none|[-—])$/i;

function comparisonValue(value: string | undefined) {
  const cleaned = (value ?? '').normalize('NFC').trim().replace(/\s+/g, ' ');
  return missingLabel.test(cleaned) ? '' : cleaned.toLocaleLowerCase('en');
}

export function comparisonRate(counts: CriterionCounts): ComparisonRate {
  const copied = poolCounts([counts]);
  const { total, resolved, unresolved } = countSummary(copied);
  return {
    counts: copied, assessed: total, resolved, unresolved,
    flaggedAmongAssessed: total ? copied.present / total : null,
    flaggedAmongResolved: resolved ? copied.present / resolved : null,
    resolutionCoverage: total ? resolved / total : null,
  };
}

function assertDocument(document: ComparisonDocument) {
  if (!document.id.trim()) throw new Error('A comparison document needs a stable ID.');
  for (const [criterion, counts] of Object.entries(document.criteria)) {
    if (countKeys.some(key => !Number.isSafeInteger(counts[key]) || counts[key] < 0)) {
      throw new Error(`Invalid comparison counts for ${document.id}, ${criterion}.`);
    }
  }
  if (document.coverage && Object.values(document.coverage).some(value => !Number.isSafeInteger(value) || value < 0)) {
    throw new Error(`Invalid source coverage for ${document.id}.`);
  }
}

function summarizeGroup(key: string, documents: ComparisonDocument[], fields: readonly ComparisonField[]): ComparisonGroup {
  const criterionIds = [...new Set(documents.flatMap(document => Object.keys(document.criteria)))].sort();
  const criteria = Object.fromEntries(criterionIds.map(criterion => {
    const counts = poolCounts(documents.map(document => document.criteria[criterion]));
    return [criterion, {
      ...comparisonRate(counts),
      missingDocumentIds: documents.filter(document => !document.criteria[criterion]).map(document => document.id),
    }];
  }));
  const coverage: ComparisonGroup['coverage'] = {
    screened: 0, eligible: 0, pages: 0, emptyPages: 0, extractionErrors: 0,
    knownDocumentIds: [], unknownDocumentIds: [], completeDocumentIds: [],
    partialDocumentIds: [], unknownCompletionDocumentIds: [],
  };
  for (const document of documents) {
    if (document.coverage) {
      coverage.knownDocumentIds.push(document.id);
      for (const field of ['screened', 'eligible', 'pages', 'emptyPages', 'extractionErrors'] as const) {
        coverage[field] += document.coverage[field];
      }
    } else coverage.unknownDocumentIds.push(document.id);
    if (document.complete === true) coverage.completeDocumentIds.push(document.id);
    else if (document.complete === false) coverage.partialDocumentIds.push(document.id);
    else coverage.unknownCompletionDocumentIds.push(document.id);
  }
  const modelRevisions = [...new Set(documents.map(document => document.modelRevision).filter((value): value is string => Boolean(value)))].sort();
  const missingModelRevisionDocumentIds = documents.filter(document => !document.modelRevision).map(document => document.id);
  const cautions = ['Documents match on the supplied labels. Evidence quality, source independence and collection conditions still require review.'];
  if (modelRevisions.length > 1) cautions.push('These documents were screened with different model revisions. Their rates need a common-model rerun before comparison.');
  if (missingModelRevisionDocumentIds.length) cautions.push('The screening model revision is missing for some documents.');
  if (coverage.partialDocumentIds.length || coverage.unknownCompletionDocumentIds.length || coverage.unknownDocumentIds.length) {
    cautions.push('Source coverage is partial or unknown for some documents.');
  }
  if (Object.values(criteria).some(criterion => criterion.missingDocumentIds.length)) {
    cautions.push('Some documents lack a result for one or more criteria. The missing document IDs accompany those rates.');
  }
  return {
    key, metadata: Object.fromEntries(fields.map(field => [field, documents[0].metadata[field]!.trim()])),
    documentIds: documents.map(document => document.id),
    documents: documents.map(document => ({
      id: document.id, label: document.label,
      criteria: Object.fromEntries(Object.entries(document.criteria).map(([criterion, counts]) => [criterion, comparisonRate(counts)])),
      complete: document.complete ?? null, coverage: document.coverage ? { ...document.coverage } : null,
      modelRevision: document.modelRevision || null,
    })),
    criteria, coverage, modelRevisions, missingModelRevisionDocumentIds, cautions,
  };
}

export function buildComparisonCohorts(documents: ComparisonDocument[], fields: readonly ComparisonField[] = comparisonFields) {
  if (!fields.length || new Set(fields).size !== fields.length || fields.some(field => !comparisonFields.includes(field))) {
    throw new Error('Choose distinct comparison fields from event, topic, genre and period.');
  }
  const groups = new Map<string, ComparisonDocument[]>();
  const ids = new Set<string>();
  const unmatched: UnmatchedDocument[] = [];
  for (const document of documents) {
    assertDocument(document);
    if (ids.has(document.id)) throw new Error(`Duplicate comparison document ID ${document.id}.`);
    ids.add(document.id);
    const missingFields = fields.filter(field => !comparisonValue(document.metadata[field]));
    if (missingFields.length) {
      unmatched.push({ id: document.id, label: document.label, reason: 'missing_metadata', missingFields });
      continue;
    }
    const key = JSON.stringify(fields.map(field => [field, comparisonValue(document.metadata[field])]));
    const group = groups.get(key) ?? [];
    group.push(document);
    groups.set(key, group);
  }
  const matchedGroups: ComparisonGroup[] = [];
  for (const [key, group] of groups) {
    if (group.length > 1) matchedGroups.push(summarizeGroup(key, group, fields));
    else unmatched.push({ id: group[0].id, label: group[0].label, reason: 'no_matching_peer', missingFields: [] });
  }
  return {
    method: 'doxa-user-metadata-cohorts-v1' as const,
    fields: [...fields], matchedGroups, unmatched,
    documentCount: documents.length,
    matchedDocumentCount: matchedGroups.reduce((sum, group) => sum + group.documentIds.length, 0),
    scope: 'Descriptive screening counts within explicit metadata groups. Matching labels alone supplies no causal, ideological or statistical-significance conclusion.',
  };
}
