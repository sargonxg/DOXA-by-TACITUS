import { predict } from './inference';
import type { Model } from './inference';
import { claimLeads } from './research-claims';
import type { ClaimKind, ClaimLead } from './research-claims';
import type { PdfPageQuality } from './pdf-layout';

export const MAX_DOCUMENT_BYTES = 32 * 1024 * 1024;
export const MAX_PDF_PAGES = 5000;
const MAX_PASSAGE = 950;
const SIDE_CONTEXT = 450;
const EXAMPLES_PER_CHECK = 12;
const MODEL_EXAMPLES_PER_CHECK = 240;

export type DocumentMode = 'political' | 'academic';
export type SourceType = 'pdf' | 'text';
export type Section = 'opening' | 'abstract' | 'introduction' | 'methods' | 'results' | 'discussion' | 'limitations' | 'conclusion' | 'references';
export type Cue = 'causal_language' | 'citation_marker' | 'study_design' | 'sample_size' | 'qualification' | 'disclosed_limitation'
  | 'sampling_risk' | 'measurement_risk' | 'confounding' | 'selective_reporting' | 'attrition' | 'generalizability';
type Range = { page?: number; firstLine: number; lastLine: number };
type Unit = Range & { text: string; section: Section; index: number };
type Counts = { eligible: number; screened: number; flagged: number; reference: number; short: number };
export type CriterionCounts = { present: number; absent: number; uncertain: number; abstained: number; unsupported: number };
export type Finding = Range & {
  category: 'model' | 'cue' | 'claim'; check: string; text: string; section: Section;
  passage: number; matched?: string; context?: string; score?: number; focus?: ClaimLead;
};
type Reservoir = { seen: number; samples: Finding[] };
export type Progress = {
  passages: number; screened: number; pages: number; totalPages: number | null;
  emptyPages: number; extractionErrors: number; bytes: number; totalBytes: number;
};
export type DocumentReport = {
  method: 'doxa-document-audit-v1'; modelRevision: string; mode: DocumentMode; source: SourceType;
  progress: Progress; complete: boolean; sections: Partial<Record<Section,Counts>>;
  criteria: Record<string,CriterionCounts>; cues: Partial<Record<Cue,number>>;
  sectionCriteria: Partial<Record<Section,Record<string,CriterionCounts>>>;
  research: { bodyPassages: number; causalWithCitationMarker: number; causalWithoutCitationMarker: number };
  claimReview: { method: 'causal-sentence-leads-v1'; counts: Partial<Record<ClaimKind,number>> };
  retainedFindings: Finding[]; sampling: { maximumPerCheck: number; maximumPerModelCheck: number; seenByCheck: Record<string,number> };
  supportPassages: Unit[];
  limitationPassages?: Unit[];
  supportLimitReached: boolean;
  sourceQuality?: (PdfPageQuality & { originalLineCount?: number; geometryScope?: 'retained_findings' })[];
  processingMs?: number;
};
export type DocumentCommand =
  | { kind: 'text'; file: File; mode: DocumentMode; modelUrl: string }
  | { kind: 'begin-pdf'; mode: DocumentMode; modelUrl: string; pages: number; bytes: number }
  | { kind: 'page'; page: number; lines: string[] | null }
  | { kind: 'finish-pdf' };
export type DocumentEvent =
  | { kind: 'status'; text: string }
  | { kind: 'ack' }
  | { kind: 'progress'; progress: Progress }
  | { kind: 'checkpoint'; report: DocumentReport }
  | { kind: 'report'; report: DocumentReport }
  | { kind: 'error'; text: string; report?: DocumentReport };

const sectionPatterns: Array<[Section,RegExp]> = [
  ['abstract', /^(abstract|summary)$/i],
  ['introduction', /^(introduction|background|related work|literature review)$/i],
  ['methods', /^(methods?|methodology|materials and methods|data and methods|data collection|research design|empirical strategy|experimental design|experimental setup(?: and testing)?|participants and procedures|operationali[sz]ation(?: and measurement)?(?: of [^.!?\n]{1,95})?|case study(?::[^.!?\n]{1,100})?)$/i],
  ['results', /^(results|findings|(?:experimental|empirical|case study|main) results)$/i],
  ['discussion', /^(discussion|results and discussion|discussion and implications)$/i],
  ['limitations', /^(limitations(?: and future (?:work|considerations|research|directions))?|study limitations|strengths and limitations)$/i],
  ['conclusion', /^(conclusions?|concluding remarks)$/i],
  ['references', /^(references|bibliography|works cited|acknowledg(?:e)?ments)$/i],
];

function heading(line: string) {
  const title = line.replace(/^\s*(?:#{1,6}\s+)?(?:\d+(?:\.\d+)*[.)]?\s+)?/, '')
    .trim().replace(/[:.]+$/, '').trim();
  return sectionPatterns.find(([,pattern]) => pattern.test(title))?.[0];
}

const cuePatterns: Array<[Cue,RegExp]> = [
  ['causal_language', /\b(?:caused|led to|resulted in|drives?|demonstrat(?:e|es|ed) that|proves? that|because of)\b/i],
  ['citation_marker', /\[\s*\d[\d,;– -]*\s*\]|\([^()\n]{1,55},?\s+(?:19|20)\d{2}[a-z]?\)|\b10\.\d{4,9}\/[\w.()/:-]+/i],
  ['study_design', /\b(?:randomi[sz]ed(?: controlled)? trial|observational study|difference[- ]in[- ]differences|regression discontinuity|meta-analysis|systematic review|case[- ]control|cross[- ]sectional|cohort study|interviews|qualitative analysis)\b/i],
  ['sample_size', /\b(?:n\s*=\s*\d[\d,]*|\d[\d,]*\s+(?:participants|respondents|observations|subjects|households|patients|interviews))\b/i],
  ['qualification', /\b(?:associated with|correlat(?:ed|ion)|may indicate|suggests?|cannot infer|cannot establish|consistent with|confidence interval|uncertain)\b/i],
  ['disclosed_limitation', /\b(?:limitation[s]?|selection bias|measurement error|small sample|self[- ]report(?:ed)?|confounding|missing data|not generalizable|cannot infer causality)\b/i],
  ['sampling_risk', /\b(?:sampling bias|selection bias|non[- ]response bias|non[- ]probability sample|convenience sample|self[- ]selection|coverage bias)\b/i],
  ['measurement_risk', /\b(?:measurement bias|measurement error|coding bias|coder disagreement|misclassification|construct validity|inter[- ]rater reliability)\b/i],
  ['confounding', /\b(?:confounding|confounders?|omitted[- ]variable bias|endogeneity|reverse causality)\b/i],
  ['selective_reporting', /\b(?:publication bias|selective reporting|selective outcome reporting|reporting bias|file drawer)\b/i],
  ['attrition', /\b(?:attrition|loss to follow[- ]up|differential dropout|missing data|missingness)\b/i],
  ['generalizability', /\b(?:external validity|generalizability|generalisation|generalization|generaliz(?:able|ed)|generalisable|representativeness)\b/i],
];

function randomIndex(value: number, maximum: number) {
  const mixed = Math.imul((value ^ (value >>> 15)), 0x2c1b3c6d);
  return ((mixed ^ (mixed >>> 12)) >>> 0) % maximum;
}

function breakAt(text: string, limit: number) {
  let end = Math.min(limit,text.length);
  if (end === text.length) return end;
  const space = text.lastIndexOf(' ',end);
  if (space > limit * .55) end = space + 1;
  // Preserve surrogate pairs where a hard boundary falls inside one.
  if (end < text.length && /[\uDC00-\uDFFF]/.test(text[end])) end--;
  return end;
}

export class DocumentAudit {
  readonly progress: Progress;
  private readonly sections: DocumentReport['sections'] = {};
  private readonly criteria: DocumentReport['criteria'] = {};
  private readonly sectionCriteria: DocumentReport['sectionCriteria'] = {};
  private readonly cues: DocumentReport['cues'] = {};
  private readonly research = { bodyPassages:0, causalWithCitationMarker:0, causalWithoutCitationMarker:0 };
  private readonly claimCounts: DocumentReport['claimReview']['counts'] = {};
  private readonly samples = new Map<string,Reservoir>();
  private readonly supportPassages: Unit[] = [];
  private readonly limitationPassages: Unit[] = [];
  private limitationCharacters=0;
  private supportCharacters=0;
  private supportLimitReached=false;
  private section: Section = 'opening';
  private buffer = '';
  private range?: Range;
  private previous?: Unit;
  private pending?: Unit;
  private index = 0;

  constructor(private readonly model: Model, readonly mode: DocumentMode, readonly source: SourceType, bytes: number) {
    this.progress = { passages:0, screened:0, pages:0, totalPages:null, emptyPages:0,
      extractionErrors:0, bytes:0, totalBytes:bytes };
    for (const criterion of model.manifest.criteria) {
      this.criteria[criterion] = { present:0, absent:0, uncertain:0, abstained:0, unsupported:0 };
    }
  }

  addLine(original: string, line: number, page?: number, continuation = false) {
    const text = original.trim();
    if (!text) {
      if (!continuation) this.flush();
      return;
    }
    const nextSection = !continuation && this.mode === 'academic' && heading(text);
    if (nextSection) {
      this.flush();
      this.section = nextSection;
      return;
    }
    let rest = text;
    while (rest.length) {
      if (this.buffer && (this.buffer.length + 1 + rest.length > MAX_PASSAGE || this.range?.page !== page)) this.flush();
      const end = breakAt(rest,MAX_PASSAGE);
      const piece = rest.slice(0,end);
      if (!this.range) this.range = {page,firstLine:line,lastLine:line};
      this.range.lastLine = line;
      this.buffer += (this.buffer ? '\n' : '') + piece;
      rest = rest.slice(end);
      if (rest.length) this.flush();
    }
  }

  finishPage() {
    this.flush();
  }

  private flush() {
    if (!this.buffer || !this.range) return;
    const fresh: Unit = { ...this.range, text:this.buffer, section:this.section, index:++this.index };
    if (this.pending) this.analyze(this.pending,this.previous,fresh);
    this.previous = this.pending;
    this.pending = fresh;
    this.range = undefined;
    this.buffer = '';
  }

  private remember(finding: Finding) {
    const id = finding.category + ':' + finding.check;
    const reservoir = this.samples.get(id) ?? { seen:0, samples:[] };
    const limit = finding.category === 'model' ? MODEL_EXAMPLES_PER_CHECK : EXAMPLES_PER_CHECK;
    reservoir.seen++;
    if (reservoir.samples.length < limit) reservoir.samples.push(finding);
    else {
      const index = randomIndex(reservoir.seen + finding.passage * 31,reservoir.seen);
      if (index < limit) reservoir.samples[index] = finding;
    }
    this.samples.set(id,reservoir);
  }

  private analyze(unit: Unit, before?: Unit, after?: Unit) {
    this.progress.passages++;
    const section = this.sections[unit.section] ?? {eligible:0, screened:0, flagged:0, reference:0, short:0};
    this.sections[unit.section] = section;
    const {index,...span}=unit;
    const excerpt = { ...span, passage:index };
    const reference = this.mode === 'academic' && unit.section === 'references';
    const prev = before?.section === unit.section ? before.text.slice(-SIDE_CONTEXT) : '';
    const next = after?.section === unit.section ? after.text.slice(0,SIDE_CONTEXT) : '';
    // A slice can start or end within a surrogate pair.
    const context = [prev.replace(/^[\uDC00-\uDFFF]/,''), next.replace(/[\uD800-\uDBFF]$/,'')]
      .filter(Boolean).join('\n\n');
    if (this.mode === 'academic') {
      let causal = false;
      let citation = false;
      for (const [cue,pattern] of cuePatterns) {
        if (reference && cue !== 'citation_marker') continue;
        const found = pattern.exec(unit.text);
        if (!found) continue;
        causal ||= cue==='causal_language';
        citation ||= cue==='citation_marker';
        this.cues[cue] = (this.cues[cue] ?? 0) + 1;
        this.remember({ ...excerpt, category:'cue',check:cue,matched:found[0],context:reference ? undefined : context });
      }
      if (!reference) {
        this.research.bodyPassages++;
        if (causal && citation) this.research.causalWithCitationMarker++;
        else if (causal) this.research.causalWithoutCitationMarker++;
      }
    }
    if (reference) { section.reference++; return; }
    if ((unit.section==='methods' || unit.section==='results') && unit.text.length>=32) {
      if (this.supportCharacters+unit.text.length<=500_000) {
        this.supportPassages.push(unit);
        this.supportCharacters+=unit.text.length;
      } else this.supportLimitReached=true;
    }
    if (unit.section==='limitations' && unit.text.length>=32 && this.limitationCharacters+unit.text.length<=200_000) {
      this.limitationPassages.push(unit);
      this.limitationCharacters+=unit.text.length;
    }
    for (const focus of claimLeads(unit.text)) {
      this.claimCounts[focus.kind] = (this.claimCounts[focus.kind] ?? 0) + 1;
      this.remember({ ...excerpt, category:'claim',check:focus.kind,focus,context });
    }
    if (unit.text.length < 32) { section.short++; return; }
    section.eligible++;
    const result = predict(this.model,unit.text,context);
    const decided = result.criteria.some(check => check.prediction_status === 'ok');
    if (decided) {
      section.screened++;
      this.progress.screened++;
    }
    if (result.criteria.some(check=>check.prediction_status==='ok' && check.suggested_decision==='present')) section.flagged++;
    const sectionCriteria = this.sectionCriteria[unit.section] ?? {};
    this.sectionCriteria[unit.section] = sectionCriteria;
    for (const check of result.criteria) {
      const counts = this.criteria[check.criterion_id];
      let decision: keyof CriterionCounts;
      if (check.prediction_status === 'unsupported_criterion') decision='unsupported';
      else if (check.prediction_status !== 'ok') decision='abstained';
      else if (check.suggested_decision === 'present') decision='present';
      else if (check.suggested_decision === 'absent') decision='absent';
      else decision='uncertain';
      const local = sectionCriteria[check.criterion_id] ?? {present:0,absent:0,uncertain:0,abstained:0,unsupported:0};
      sectionCriteria[check.criterion_id] = local;
      counts[decision]++;
      local[decision]++;
      if (decision==='present') {
        this.remember({ ...excerpt,category:'model',check:check.criterion_id,score:check.scores?.present,context });
      }
    }
  }

  snapshot(complete: boolean): DocumentReport {
    if (complete) {
      this.flush();
      if (this.pending) this.analyze(this.pending,this.previous);
      this.pending = undefined;
      this.previous = undefined;
    }
    return {
      method:'doxa-document-audit-v1',modelRevision:this.model.manifest.model_revision,mode:this.mode,source:this.source,
      progress:{...this.progress},complete,sections:this.sections,criteria:this.criteria,sectionCriteria:this.sectionCriteria,cues:this.cues,research:this.research,
      claimReview:{method:'causal-sentence-leads-v1',counts:this.claimCounts},
      retainedFindings:[...this.samples.values()].flatMap(value => value.samples).sort((a,b) => a.passage - b.passage),
      supportPassages:complete ? this.supportPassages : [],
      limitationPassages:complete ? this.limitationPassages : [],
      supportLimitReached:this.supportLimitReached,
      sampling:{maximumPerCheck:EXAMPLES_PER_CHECK,maximumPerModelCheck:MODEL_EXAMPLES_PER_CHECK,seenByCheck:Object.fromEntries(
        [...this.samples].map(([key,value]) => [key,value.seen])),
      },
    };
  }
}
