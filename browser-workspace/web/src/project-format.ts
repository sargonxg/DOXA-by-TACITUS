import type { ClaimResult } from './claim-contract';
import type { DocumentReport, Finding } from './document-audit';
import type { Decision } from './review-case';
import type { ResearchOrigin } from './research-sources';

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type ProjectDocument = {
  file: File;
  author: string;
  doi?: string;
  researchSource?: ResearchOrigin;
  report: DocumentReport;
  sample: boolean;
  metadata?: Record<string, string>;
  sha256?: string;
  [key: string]: unknown;
};
export type ProjectSnapshot = {
  documents: ProjectDocument[];
  decisions: [string, Decision][];
  evidenceChecks: [string, { document: number; result: ClaimResult }][];
  selectedDocument: number;
  failures: string[];
  canceled: boolean;
  extra?: Record<string, JsonValue>;
  [key: string]: unknown;
};
export type ProjectRecord = {
  format: 'doxa-project';
  version: 1;
  id: string;
  name: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
  snapshot: ProjectSnapshot;
};
export type ProjectOptions = { signal?: AbortSignal };
export type ProjectErrorCode = 'invalid' | 'aborted' | 'conflict' | 'quota' | 'not_found' | 'unavailable';

export class ProjectError extends Error {
  readonly code: ProjectErrorCode;
  constructor(code: ProjectErrorCode, message: string) {
    super(message);
    this.code = code;
    this.name = code === 'aborted' ? 'AbortError' : 'ProjectError';
  }
}

export const MAX_PROJECT_SOURCE_BYTES = 128 * 1024 * 1024;
export const MAX_PROJECT_MANIFEST_BYTES = 64 * 1024 * 1024;
const MAX_FILE_BYTES = 32 * 1024 * 1024;
const MAGIC = new TextEncoder().encode('DOXA-PROJECT\n');
const HEADER_BYTES = MAGIC.length + 4 + 32;
const MIME = 'application/vnd.tacitus.doxa-project';
const hashPattern = /^[a-f0-9]{64}$/;
const dispositions = new Set(['confirmed', 'dismissed', 'needs_context']);
const verdicts = new Set(['supported', 'contradicted', 'insufficient_context']);

function ensure(condition: unknown, message: string): asserts condition {
  if (!condition) throw new ProjectError('invalid', message);
}

export function checkProjectAbort(signal?: AbortSignal) {
  if (signal?.aborted) throw new ProjectError('aborted', 'Project operation canceled.');
}

function record(value: unknown, name: string): Record<string, unknown> {
  ensure(value !== null && typeof value === 'object' && !Array.isArray(value), name + ' must be an object.');
  const prototype = Object.getPrototypeOf(value);
  ensure(prototype === Object.prototype || prototype === null, name + ' must contain plain data.');
  return value as Record<string, unknown>;
}

function integer(value: unknown, name: string, maximum = 1_000_000_000) {
  ensure(typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= maximum, name + ' is invalid.');
  return value;
}

function finite(value: unknown, name: string, minimum = -1_000_000_000, maximum = 1_000_000_000) {
  ensure(typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum, name + ' is invalid.');
  return value;
}

function text(value: unknown, name: string, maximum = MAX_PROJECT_MANIFEST_BYTES) {
  ensure(typeof value === 'string' && value.length <= maximum, name + ' is invalid.');
  return value;
}

function array(value: unknown, name: string, maximum = 100_000): unknown[] {
  ensure(Array.isArray(value) && value.length <= maximum, name + ' is invalid.');
  return value;
}

function webUrl(value: unknown, name: string) {
  const source = text(value, name, 5000);
  let url: URL;
  try { url = new URL(source); } catch { throw new ProjectError('invalid', name + ' must be an HTTP or HTTPS URL.'); }
  ensure(['https:', 'http:'].includes(url.protocol) && !url.username && !url.password, name + ' must be an HTTP or HTTPS URL.');
}

function date(value: unknown, name: string) {
  const source = text(value, name, 80);
  ensure(Number.isFinite(Date.parse(source)), name + ' is invalid.');
  return source;
}

function jsonData(value: unknown, ancestors = new Set<object>(), depth = 0, budget = { nodes: 0 }) {
  ensure(depth <= 40 && ++budget.nodes <= 2_000_000, 'Project metadata is too complex.');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') { ensure(Number.isFinite(value), 'Project metadata contains a non-finite number.'); return; }
  ensure(typeof value === 'object', 'Project metadata must contain JSON data.');
  ensure(!ancestors.has(value), 'Project metadata contains a circular reference.');
  ancestors.add(value);
  if (Array.isArray(value)) {
    ensure(value.length <= 100_000, 'Project metadata contains an oversized list.');
    for (const item of value) jsonData(item, ancestors, depth + 1, budget);
  } else {
    for (const [key, item] of Object.entries(record(value, 'Project metadata'))) {
      ensure(key.length <= 1024 && !['__proto__', 'prototype', 'constructor'].includes(key), 'Project metadata contains an unsafe property.');
      // Undefined optional fields disappear in the JSON representation.
      if (item !== undefined) jsonData(item, ancestors, depth + 1, budget);
    }
  }
  ancestors.delete(value);
}

function range(value: Record<string, unknown>, name: string, pages: number | null) {
  const first = integer(value.firstLine, name + ' first line');
  const last = integer(value.lastLine, name + ' last line');
  ensure(first >= 1 && last >= first, name + ' has an invalid source range.');
  if (value.page !== undefined) {
    const page = integer(value.page, name + ' page', 5000);
    ensure(page >= 1 && pages !== null && page <= pages, name + ' refers to an unavailable PDF page.');
  }
}

function finding(value: unknown, pages: number | null): Finding {
  const item = record(value, 'Finding');
  ensure(['model', 'cue', 'claim'].includes(String(item.category)), 'Finding category is invalid.');
  text(item.check, 'Finding criterion', 200);
  const source = text(item.text, 'Finding text');
  text(item.section, 'Finding section', 200);
  ensure(integer(item.passage, 'Finding passage') >= 1, 'Finding passage is invalid.');
  range(item, 'Finding', pages);
  if (item.context !== undefined) text(item.context, 'Finding context');
  if (item.matched !== undefined) ensure(source.includes(text(item.matched, 'Matched wording')), 'Matched wording differs from the source.');
  if (item.focus !== undefined) {
    const focus = record(item.focus, 'Sentence focus');
    const start = integer(focus.start, 'Sentence start', source.length);
    const end = integer(focus.end, 'Sentence end', source.length);
    const matchStart = integer(focus.matchStart, 'Wording start', source.length);
    const matchEnd = integer(focus.matchEnd, 'Wording end', source.length);
    ensure(start <= matchStart && matchStart < matchEnd && matchEnd <= end, 'Sentence focus exceeds its original passage.');
    if (focus.citation !== undefined) {
      const citation = record(focus.citation, 'Citation');
      const citationStart = integer(citation.start, 'Citation start', source.length);
      const citationEnd = integer(citation.end, 'Citation end', source.length);
      ensure(start <= citationStart && citationStart < citationEnd && citationEnd <= end, 'Citation exceeds its original sentence.');
    }
  }
  return item as Finding;
}

function countMap(value: unknown, name: string) {
  const counts = record(value, name);
  for (const [key, count] of Object.entries(counts)) integer(count, name + ' ' + key);
}

function criterionMap(value: unknown) {
  const criteria = record(value, 'Criterion counts');
  for (const [name, value] of Object.entries(criteria)) {
    const counts = record(value, name);
    for (const key of ['present', 'absent', 'uncertain', 'abstained', 'unsupported']) {
      if (counts[key] === undefined) counts[key] = 0;
      integer(counts[key], name + ' ' + key);
    }
  }
}

function evidencePassage(value: unknown, name: string, pages: number | null, sections: string[]) {
  const passage = record(value, name);
  text(passage.text, name + ' text');
  range(passage, name, pages);
  ensure(sections.includes(String(passage.section)), name + ' section is invalid.');
  ensure(integer(passage.index, name + ' index') >= 1, name + ' index is invalid.');
  return passage;
}

function pdfBox(value: unknown, name: string) {
  const box = array(value, name, 4);
  ensure(box.length === 4, name + ' must contain four coordinates.');
  box.forEach((coordinate, index) => finite(coordinate, name + ' coordinate', index < 2 ? -1_000_000_000 : 0));
}

function sourceQuality(value: unknown, pages: number | null) {
  const entries = array(value, 'PDF extraction quality', 5000);
  const pageNumbers = new Set<number>();
  ensure(!entries.length || pages !== null, 'PDF extraction quality requires PDF pages.');
  for (const value of entries) {
    const quality = record(value, 'PDF page quality');
    const page = integer(quality.page, 'Extraction quality page', pages ?? 0);
    ensure(page >= 1 && !pageNumbers.has(page), 'Extraction quality pages are invalid or repeated.');
    pageNumbers.add(page);
    finite(quality.width, 'PDF page width', 0);
    finite(quality.height, 'PDF page height', 0);
    ensure(['text_layer', 'ocr', 'image_only', 'no_selectable_text', 'error'].includes(String(quality.source)), 'PDF extraction source is invalid.');
    ensure(['geometric_rows', 'column_heuristic', 'native_content'].includes(String(quality.readingOrder)), 'PDF reading order is invalid.');
    for (const key of ['nativeCharacters', 'characters', 'columns', 'tableRows', 'smallTextLines', 'imageOperators']) {
      integer(quality[key], 'PDF ' + key);
    }
    if (quality.ocrConfidence !== undefined) finite(quality.ocrConfidence, 'OCR confidence', 0, 100);
    if (quality.geometryScope !== undefined) ensure(quality.geometryScope === 'retained_findings', 'PDF geometry scope is invalid.');
    const lines = array(quality.lines, 'PDF line geometry');
    if (quality.geometryScope === 'retained_findings') {
      ensure(quality.originalLineCount !== undefined, 'Retained PDF geometry requires the original line count.');
    }
    const lineCount = quality.originalLineCount === undefined ? lines.length : integer(quality.originalLineCount, 'Original PDF line count', 100_000);
    ensure(lines.length <= lineCount, 'PDF line geometry exceeds the original line count.');
    const lineNumbers = new Set<number>();
    for (const value of lines) {
      const line = record(value, 'PDF line location');
      const index = integer(line.line, 'PDF line number', lineCount);
      ensure(index >= 1 && !lineNumbers.has(index), 'PDF line numbers are invalid or repeated.');
      lineNumbers.add(index);
      pdfBox(line.box, 'PDF line box');
      for (const fragment of array(line.fragments, 'PDF text fragments')) integer(fragment, 'PDF text fragment');
      ensure(['text_layer', 'ocr'].includes(String(line.source)), 'PDF line source is invalid.');
      if (line.confidence !== undefined) finite(line.confidence, 'PDF line confidence', 0, 100);
    }
    for (const value of array(quality.excludedMargins, 'Excluded PDF margins')) {
      const margin = record(value, 'Excluded PDF margin');
      ensure(integer(margin.line, 'Excluded margin line', lineCount) >= 1, 'Excluded margin line is invalid.');
      text(margin.text, 'Excluded margin text');
      pdfBox(margin.box, 'Excluded margin box');
      ensure(margin.reason === 'repeated_margin', 'Excluded margin reason is invalid.');
    }
    for (const issue of array(quality.issues, 'PDF extraction issues', 1000)) text(issue, 'PDF extraction issue', 1000);
  }
}

function normalizeReport(value: unknown, file: File): DocumentReport {
  const source = record(value, 'Document report');
  ensure(source.method === 'doxa-document-audit-v1', 'This project uses an unsupported document report version.');
  ensure(['academic', 'political'].includes(String(source.mode)), 'Document audit mode is invalid.');
  const isPdf = file.name.toLowerCase().endsWith('.pdf');
  ensure(source.source === (isPdf ? 'pdf' : 'text'), 'Document report and source file type differ.');
  text(source.modelRevision, 'Screening model revision', 300);
  ensure(typeof source.complete === 'boolean', 'Document completion state is invalid.');
  const progress = { totalPages: isPdf ? 0 : null, ...record(source.progress, 'Document progress') };
  const p = progress as Record<string, unknown>;
  for (const key of ['passages', 'screened', 'pages', 'emptyPages', 'extractionErrors', 'bytes', 'totalBytes']) integer(p[key], 'Document ' + key);
  ensure(p.totalBytes === file.size, 'Source file size differs from its report.');
  ensure((p.bytes as number) <= file.size && (p.screened as number) <= (p.passages as number), 'Document progress is inconsistent.');
  const pages = p.totalPages === null ? null : integer(p.totalPages, 'PDF page count', 5000);
  ensure(!isPdf || pages !== null, 'PDF page count is missing.');
  ensure(pages === null ? p.pages === 0 : (p.pages as number) <= pages, 'PDF page coverage is inconsistent.');
  const report: Record<string, unknown> = {
    ...source, progress, sectionCriteria: source.sectionCriteria ?? {},
    supportPassages: source.supportPassages ?? [], supportLimitReached: source.supportLimitReached ?? false,
    claimReview: source.claimReview ?? { method: 'causal-sentence-leads-v1', counts: {} },
    research: source.research ?? { bodyPassages: 0, causalWithCitationMarker: 0, causalWithoutCitationMarker: 0 },
  };
  criterionMap(report.criteria);
  for (const section of Object.values(record(report.sectionCriteria, 'Section criteria'))) criterionMap(section);
  for (const value of Object.values(record(report.sections, 'Section coverage'))) {
    const section = record(value, 'Section coverage');
    for (const key of ['eligible', 'screened', 'flagged', 'reference', 'short']) integer(section[key], 'Section ' + key);
  }
  countMap(report.cues, 'Research cues');
  countMap(record(report.claimReview, 'Causal wording').counts, 'Causal wording counts');
  for (const item of array(report.retainedFindings, 'Retained findings')) finding(item, pages);
  for (const unit of array(report.supportPassages, 'Evidence passages')) evidencePassage(unit, 'Evidence passage', pages, ['methods', 'results']);
  if (report.limitationPassages !== undefined) {
    for (const unit of array(report.limitationPassages, 'Limitation passages')) evidencePassage(unit, 'Limitation passage', pages, ['limitations']);
  }
  if (report.sourceQuality !== undefined) sourceQuality(report.sourceQuality, pages);
  if (report.processingMs !== undefined) finite(report.processingMs, 'Document processing time', 0, Number.MAX_SAFE_INTEGER);
  const sampling = record(report.sampling, 'Excerpt sampling');
  if (sampling.maximumPerModelCheck === undefined) sampling.maximumPerModelCheck = sampling.maximumPerCheck;
  integer(sampling.maximumPerCheck, 'Excerpt cap', 100_000);
  integer(sampling.maximumPerModelCheck, 'Model excerpt cap', 100_000);
  countMap(sampling.seenByCheck, 'Excerpt counts');
  return report as DocumentReport;
}

function splitsSurrogatePair(source: string, offset: number) {
  return offset > 0 && offset < source.length && /[\uD800-\uDBFF]/.test(source[offset - 1]) && /[\uDC00-\uDFFF]/.test(source[offset]);
}

function reviewerText(value: unknown, name: string, maximum: number) {
  const content = text(value, name, maximum);
  ensure(!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(content), name + ' contains invalid control characters.');
  return content;
}

function findingAnnotations(value: unknown, source: string) {
  const annotations = record(value, 'Reviewer annotations');
  ensure(annotations.provenance === 'reviewer_entered', 'Annotation provenance is invalid.');
  reviewerText(annotations.speaker, 'Annotated speaker', 300);
  reviewerText(annotations.target, 'Annotated target', 300);
  reviewerText(annotations.scopeNote, 'Annotation scope note', 2000);
  const selections = new Set<string>();
  for (const value of array(annotations.evidence, 'Reviewer evidence', 1000)) {
    const evidence = record(value, 'Reviewer evidence');
    ensure(['supporting', 'countervailing'].includes(String(evidence.role)), 'Reviewer evidence role is invalid.');
    const start = integer(evidence.start, 'Evidence selection start', source.length);
    const end = integer(evidence.end, 'Evidence selection end', source.length);
    const content = text(evidence.text, 'Reviewer evidence text', source.length);
    ensure(start < end && !splitsSurrogatePair(source, start) && !splitsSurrogatePair(source, end)
      && content === source.slice(start, end) && Boolean(content.trim()), 'Reviewer evidence differs from its exact source selection.');
    const key = String(evidence.role) + ':' + start + ':' + end;
    ensure(!selections.has(key), 'Reviewer evidence contains a duplicate selection.');
    selections.add(key);
  }
}

function researchOrigin(value: unknown) {
  const source = record(value, 'Research source');
  for (const key of ['documentId', 'passageId', 'title', 'sourceXPath']) text(source[key], 'Research ' + key);
  ensure(integer(source.paragraphIndex, 'Research paragraph') >= 1, 'Research paragraph is invalid.');
  ensure(hashPattern.test(String(source.textSha256)), 'Research source hash is invalid.');
  webUrl(source.sourceUrl, 'Research source URL');
  const license = record(source.license, 'Research license');
  text(license.identifier, 'Research license identifier', 300);
  if (license.url !== null && license.url !== undefined) webUrl(license.url, 'Research license URL');
  for (const topic of array(source.topicCandidates, 'Research topics', 1000)) text(topic, 'Research topic', 300);
}

function validateDecisions(value: unknown, documents: ProjectDocument[]): [string, Decision][] {
  const entries = array(value, 'Review decisions', 30_000);
  const keys = new Set<string>();
  for (const entry of entries) {
    const pair = array(entry, 'Review decision entry', 2);
    ensure(pair.length === 2, 'Review decision entry is incomplete.');
    const key = text(pair[0], 'Review decision key', 1000);
    ensure(!keys.has(key), 'Review decisions contain a duplicate key.');
    keys.add(key);
    const decision = record(pair[1], 'Review decision');
    const index = integer(decision.document, 'Review document index', documents.length);
    ensure(index < documents.length, 'Review decision refers to an unavailable source.');
    const report = documents[index].report;
    const selected = finding(decision.finding, report.progress.totalPages);
    ensure(report.retainedFindings.some(item => item.category === selected.category && item.check === selected.check
      && item.passage === selected.passage && item.text === selected.text && item.firstLine === selected.firstLine
      && item.lastLine === selected.lastLine && item.page === selected.page && item.focus?.start === selected.focus?.start),
    'A review decision differs from its retained source finding.');
    ensure(dispositions.has(String(decision.disposition)), 'Review outcome is invalid.');
    text(decision.note, 'Reviewer note', 30_000);
    date(decision.recordedAt, 'Review timestamp');
    if (decision.annotations !== undefined) findingAnnotations(decision.annotations, selected.text);
    if (decision.mitigation !== undefined) {
      const draft = record(decision.mitigation, 'Mitigation draft');
      ensure(draft.status === 'reviewer_draft', 'Mitigation provenance is invalid.');
      text(draft.proposedText, 'Proposed wording', 30_000);
      text(draft.purpose, 'Revision purpose', 10_000);
      const checks = record(draft.checks, 'Meaning checks');
      for (const key of ['scope', 'position', 'evidence']) ensure(typeof checks[key] === 'boolean', 'Meaning check is invalid.');
    }
  }
  return entries as [string, Decision][];
}

function studyPacket(value: unknown, claim: string, report: DocumentReport) {
  const packet = record(value, 'Study context packet');
  ensure(packet.method === 'doxa-study-context-v1', 'Study context method is invalid.');
  ensure(text(packet.claim, 'Study context claim') === claim, 'Study context refers to a different claim.');
  const statuses = new Set<string>();
  const register = (value: unknown) => {
    const section = text(value, 'Study context section', 30);
    ensure(['methods', 'results', 'limitations'].includes(section) && !statuses.has(section), 'Study context sections are invalid or repeated.');
    statuses.add(section);
    return section;
  };
  for (const value of array(packet.sections, 'Study context selections', 3)) {
    const entry = record(value, 'Study context selection');
    const section = register(entry.section);
    ensure(entry.selection === 'lexical_candidate', 'Study context selection provenance is invalid.');
    const window = record(entry.window, 'Study context window');
    text(window.text, 'Study context text');
    range(window, 'Study context window', report.progress.totalPages);
    if (window.relevance !== undefined) finite(window.relevance, 'Study context relevance', 0);
    if (window.section !== undefined) ensure(window.section === section, 'Study context source section differs.');
    if (window.index !== undefined) ensure(integer(window.index, 'Study context passage index') >= 1, 'Study context passage index is invalid.');
    const pool = section === 'limitations' ? report.limitationPassages ?? [] : report.supportPassages;
    ensure(pool.some(passage => passage.section === section && passage.text === window.text && passage.page === window.page
      && passage.firstLine === window.firstLine && passage.lastLine === window.lastLine
      && (window.index === undefined || passage.index === window.index)), 'Study context differs from its retained source passage.');
  }
  for (const name of ['unavailable', 'noMatchingPassage']) {
    for (const section of array(packet[name], 'Study context ' + name, 3)) register(section);
  }
  ensure(statuses.size === 3, 'Study context must account for Methods, Results and Limitations.');
}

function validateEvidenceChecks(value: unknown, documents: ProjectDocument[]): ProjectSnapshot['evidenceChecks'] {
  const entries = array(value, 'Evidence checks', 30_000);
  const keys = new Set<string>();
  for (const entry of entries) {
    const pair = array(entry, 'Evidence check entry', 2);
    ensure(pair.length === 2, 'Evidence check entry is incomplete.');
    const key = text(pair[0], 'Evidence check key', 1000);
    ensure(!keys.has(key), 'Evidence checks contain a duplicate key.');
    keys.add(key);
    const check = record(pair[1], 'Evidence check');
    const index = integer(check.document, 'Evidence document index', documents.length);
    ensure(index < documents.length, 'Evidence check refers to an unavailable source.');
    const result = record(check.result, 'Evidence result');
    text(result.method, 'Evidence method', 300);
    const claim = text(result.claim, 'Checked claim');
    ensure(verdicts.has(String(result.verdict)), 'Evidence verdict is invalid.');
    for (const key of ['passages', 'shortlisted', 'screened', 'unreadablePages', 'emptyPages']) integer(result[key], 'Evidence ' + key);
    for (const value of array(result.leads, 'Evidence leads', 64)) {
      const lead = record(value, 'Evidence lead');
      ensure(verdicts.has(String(lead.verdict)), 'Evidence lead verdict is invalid.');
      const window = record(lead.window, 'Evidence window');
      text(window.text, 'Evidence window text');
      range(window, 'Evidence window', documents[index].report.progress.totalPages);
    }
    if (result.studyPacket !== undefined) studyPacket(result.studyPacket, claim, documents[index].report);
  }
  return entries as ProjectSnapshot['evidenceChecks'];
}

function hex(bytes: ArrayBuffer) {
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
}

async function digest(blob: Blob, signal?: AbortSignal) {
  checkProjectAbort(signal);
  const bytes = await blob.arrayBuffer();
  checkProjectAbort(signal);
  const result = await crypto.subtle.digest('SHA-256', bytes);
  checkProjectAbort(signal);
  return hex(result);
}

async function canonicalFile(value: unknown, signal?: AbortSignal) {
  ensure(value instanceof File, 'A project source file is missing.');
  ensure(value.size > 0 && value.size <= MAX_FILE_BYTES, 'Each source file must contain at most 32 MiB.');
  const name = value.name;
  ensure(/\.(pdf|txt|md)$/i.test(name) && name.length <= 1000 && !name.includes('\0'), 'Source filename is invalid.');
  const pdf = name.toLowerCase().endsWith('.pdf');
  checkProjectAbort(signal);
  if (pdf) {
    const header = await value.slice(0, 1024).text();
    ensure(header.includes('%PDF-'), 'A PDF source has an invalid file signature.');
  } else {
    try { new TextDecoder('utf-8', { fatal: true }).decode(await value.arrayBuffer()); }
    catch { throw new ProjectError('invalid', 'Text sources must use valid UTF-8.'); }
  }
  checkProjectAbort(signal);
  return new File([value], name, { type: pdf ? 'application/pdf' : name.toLowerCase().endsWith('.md') ? 'text/markdown' : 'text/plain', lastModified: value.lastModified });
}

function copyProjectData(value: unknown) {
  try { return structuredClone(value); }
  catch { throw new ProjectError('invalid', 'Project data could not be copied safely.'); }
}

async function normalizeSnapshot(value: unknown, options: ProjectOptions): Promise<ProjectSnapshot> {
  const source = record(value, 'Project snapshot');
  const documents: ProjectDocument[] = [];
  let sourceBytes = 0;
  for (const raw of array(source.documents, 'Project documents', 12)) {
    const document = record(raw, 'Project document');
    const file = await canonicalFile(document.file, options.signal);
    sourceBytes += file.size;
    ensure(sourceBytes <= MAX_PROJECT_SOURCE_BYTES, 'Project sources exceed 128 MiB.');
    const sha256 = await digest(file, options.signal);
    if (document.sha256 !== undefined) ensure(document.sha256 === sha256, 'Source file checksum does not match the saved project.');
    const author = text(document.author ?? '', 'Author label', 2000);
    if (document.sample !== undefined) ensure(typeof document.sample === 'boolean', 'Example provenance is invalid.');
    if (document.doi !== undefined) text(document.doi, 'Source DOI', 1000);
    if (document.metadata !== undefined) {
      for (const value of Object.values(record(document.metadata, 'Comparison metadata'))) text(value, 'Comparison metadata value', 5000);
    }
    if (document.researchSource !== undefined) researchOrigin(document.researchSource);
    const report = normalizeReport(document.report, file);
    documents.push({ ...document, file, report, author, sample: document.sample === true, sha256 } as ProjectDocument);
  }
  const selected = integer(source.selectedDocument ?? 0, 'Selected document', documents.length);
  ensure(documents.length ? selected < documents.length : selected === 0, 'Selected document is unavailable.');
  if (source.canceled !== undefined) ensure(typeof source.canceled === 'boolean', 'Cancellation state is invalid.');
  const normalized = {
    ...source, documents, selectedDocument: selected,
    decisions: validateDecisions(source.decisions ?? [], documents),
    evidenceChecks: validateEvidenceChecks(source.evidenceChecks ?? [], documents),
    failures: array(source.failures ?? [], 'Failed sources', 12).map(value => text(value, 'Failed source name', 1000)),
    canceled: source.canceled === true,
  } as ProjectSnapshot;
  const serializable = { ...normalized, documents: documents.map(({ file: _, ...document }) => document) };
  jsonData(serializable);
  ensure(new TextEncoder().encode(JSON.stringify(serializable)).length <= MAX_PROJECT_MANIFEST_BYTES, 'Project report metadata exceeds 64 MiB.');
  checkProjectAbort(options.signal);
  return normalized;
}

export async function prepareProjectSnapshot(value: unknown, options: ProjectOptions = {}): Promise<ProjectSnapshot> {
  checkProjectAbort(options.signal);
  return normalizeSnapshot(copyProjectData(value), options);
}

export async function prepareProjectRecord(value: unknown, options: ProjectOptions = {}): Promise<ProjectRecord> {
  checkProjectAbort(options.signal);
  const source = record(copyProjectData(value), 'Project');
  ensure(source.format === 'doxa-project' && source.version === 1, 'This project format version is unsupported.');
  const id = text(source.id, 'Project ID', 200);
  ensure(id.length > 0 && /^[\w.-]+$/.test(id), 'Project ID is invalid.');
  const name = text(source.name, 'Project name', 120).trim();
  ensure(name.length > 0, 'Give the project a name.');
  const revision = integer(source.revision, 'Project revision');
  const createdAt = date(source.createdAt, 'Project creation time');
  const updatedAt = date(source.updatedAt, 'Project save time');
  const snapshot = await normalizeSnapshot(source.snapshot, options);
  const normalized = { ...source, format: 'doxa-project' as const, version: 1 as const, id, name, revision, createdAt, updatedAt, snapshot };
  jsonData({ ...normalized, snapshot: { ...snapshot, documents: snapshot.documents.map(({ file: _, ...document }) => document) } });
  return normalized;
}

export async function exportProject(value: ProjectRecord, options: ProjectOptions = {}): Promise<Blob> {
  const project = await prepareProjectRecord(value, options);
  const files = project.snapshot.documents.map(document => ({ name: document.file.name, type: document.file.type,
    size: document.file.size, lastModified: document.file.lastModified, sha256: document.sha256 }));
  const manifest = { format: 'doxa-project-archive', version: 1, project: { ...project,
    snapshot: { ...project.snapshot, documents: project.snapshot.documents.map(({ file: _, ...document }) => document) } }, files };
  const bytes = new TextEncoder().encode(JSON.stringify(manifest));
  ensure(bytes.length <= MAX_PROJECT_MANIFEST_BYTES, 'Project report metadata exceeds 64 MiB.');
  const checksum = await crypto.subtle.digest('SHA-256', bytes);
  checkProjectAbort(options.signal);
  const header = new Uint8Array(HEADER_BYTES);
  header.set(MAGIC);
  new DataView(header.buffer).setUint32(MAGIC.length, bytes.length, false);
  header.set(new Uint8Array(checksum), MAGIC.length + 4);
  return new Blob([header, bytes, ...project.snapshot.documents.map(document => document.file)], { type: MIME });
}

export async function importProject(blob: Blob, options: ProjectOptions = {}): Promise<ProjectRecord> {
  ensure(blob instanceof Blob && blob.size >= HEADER_BYTES && blob.size <= HEADER_BYTES + MAX_PROJECT_MANIFEST_BYTES + MAX_PROJECT_SOURCE_BYTES, 'Choose a valid DOXA project archive within the size limits.');
  checkProjectAbort(options.signal);
  const header = new Uint8Array(await blob.slice(0, HEADER_BYTES).arrayBuffer());
  ensure(MAGIC.every((byte, index) => header[index] === byte), 'This file is not a DOXA project archive.');
  const length = new DataView(header.buffer).getUint32(MAGIC.length, false);
  ensure(length > 0 && length <= MAX_PROJECT_MANIFEST_BYTES && HEADER_BYTES + length <= blob.size, 'Project archive metadata length is invalid.');
  const manifestBytes = await blob.slice(HEADER_BYTES, HEADER_BYTES + length).arrayBuffer();
  const manifestHash = await crypto.subtle.digest('SHA-256', manifestBytes);
  ensure(hex(manifestHash) === hex(header.slice(MAGIC.length + 4).buffer), 'Project archive metadata checksum failed.');
  let parsed: unknown;
  try { parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(manifestBytes)); }
  catch { throw new ProjectError('invalid', 'Project archive metadata is unreadable.'); }
  jsonData(parsed);
  const manifest = record(parsed, 'Project archive');
  ensure(manifest.format === 'doxa-project-archive' && manifest.version === 1, 'This project archive version is unsupported.');
  const project = record(manifest.project, 'Project');
  const snapshot = record(project.snapshot, 'Project snapshot');
  const documents = array(snapshot.documents, 'Project documents', 12);
  const files = array(manifest.files, 'Project file manifest', 12);
  ensure(files.length === documents.length, 'Project file manifest and source count differ.');
  let offset = HEADER_BYTES + length;
  const restored = [];
  for (const [index, descriptor] of files.entries()) {
    checkProjectAbort(options.signal);
    const file = record(descriptor, 'Project file');
    const size = integer(file.size, 'Source byte count', MAX_FILE_BYTES);
    ensure(size > 0 && offset + size <= blob.size, 'Project source bytes are missing.');
    const name = text(file.name, 'Source filename', 1000);
    const type = text(file.type, 'Source type', 300);
    const lastModified = integer(file.lastModified, 'Source modification time', Number.MAX_SAFE_INTEGER);
    ensure(hashPattern.test(String(file.sha256)), 'Project source checksum is invalid.');
    const restoredFile = new File([blob.slice(offset, offset + size)], name, { type, lastModified });
    offset += size;
    const document = record(documents[index], 'Project document');
    if (document.sha256 !== undefined) ensure(document.sha256 === file.sha256, 'Project file manifest hashes disagree.');
    restored.push({ ...document, file: restoredFile, sha256: file.sha256 });
  }
  ensure(offset === blob.size, 'Project archive contains unexpected trailing bytes.');
  return prepareProjectRecord({ ...project, snapshot: { ...snapshot, documents: restored } }, options);
}
