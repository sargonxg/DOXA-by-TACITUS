import type { DocumentReport, Finding } from './document-audit';

export type ReadingQuestion = {
  id: string;
  title: string;
  question: string;
  evidenceNeeded: string[];
};

export type SourceObservation = {
  kind: 'recorded_cue' | 'causal_wording' | 'citation_marker' | 'quotation' | 'reporting_wording' | 'scope_wording';
  text: string;
  start: number;
  end: number;
  readingQuestion: string;
};

export type FindingExplanation = {
  method: 'doxa-source-reading-v1';
  source: {
    text: string;
    context: string;
    page?: number;
    firstLine: number;
    lastLine: number;
    section: string;
    focusedText?: string;
  };
  basis: {
    kind: 'model_flag' | 'recorded_text_cue' | 'causal_reading_rule';
    description: string;
    learnedEvidenceSpan: false;
    modelScore?: number;
    modelScoreStatus?: 'uncalibrated_class_score';
  };
  observations: SourceObservation[];
  roles: {
    speaker: { status: 'requires_review'; question: string };
    target: { status: 'requires_review'; question: string };
  };
  questions: ReadingQuestion[];
  boundaries: string[];
  limitations: string[];
};

type ReadingGuide = { questions: ReadingQuestion[]; boundaries: string[] };
type ReportContext = Pick<DocumentReport, 'complete' | 'source' | 'supportLimitReached'>;

const question = (id: string, title: string, text: string, evidenceNeeded: string[]): ReadingQuestion => ({
  id, title, question: text, evidenceNeeded,
});

const guides: Record<string, ReadingGuide> = {
  evaluative_framing: {
    questions: [
      question('evaluation', 'Locate the evaluation', 'Which exact wording evaluates an identifiable actor, action or policy?', ['Reviewer-selected wording', 'Actor or policy referent']),
      question('attribution', 'Identify the stance', 'Does the focal author endorse, quote, reject or attribute that evaluation?', ['Speaker and quotation scope', 'Surrounding attribution']),
      question('grounds', 'Check the grounds', 'Does the source establish the factual grounds for the evaluation, and is the judgment presented as advocacy?', ['Evidence for the stated conduct', 'Declared genre and purpose']),
    ],
    boundaries: ['Supported criticism and declared political advocacy can contain evaluative wording.', 'An attributed quotation can document another speaker’s position.'],
  },
  collective_blame: {
    questions: [
      question('actor', 'Identify who acted', 'Which actors performed the conduct described in this passage?', ['Specific act and responsible actor', 'Source establishing the act']),
      question('extension', 'Check the extension of responsibility', 'Which wording extends responsibility beyond those actors, and what is the membership basis?', ['Reviewer-selected responsibility span', 'Group and individual referents']),
    ],
    boundaries: ['A government, cabinet or institution can be accountable for its own decisions.', 'A pronoun or group name needs its actual referent before the scope of blame can be resolved.'],
  },
  identity_stereotyping: {
    questions: [
      question('trait', 'Identify the trait and group', 'What trait is ascribed to which people, and which text establishes the membership link?', ['Trait wording', 'Group referent and scope']),
      question('scope', 'Compare the claim with its scope', 'Does the claim exceed the individuals, sample or behavior actually described?', ['Relevant population or sample', 'Evidence supporting the generalization']),
    ],
    boundaries: ['A bounded empirical description needs its method and uncertainty.', 'The same expression can be quoted, challenged or discussed without endorsement.'],
  },
  identity_derogation_exclusion: {
    questions: [
      question('target', 'Locate the target and treatment', 'Who is targeted, and what degradation or exclusion is expressed?', ['Target referent', 'Reviewer-selected treatment span']),
      question('basis', 'Check the stated basis', 'Does the treatment rest on identity, specific conduct or a relevant eligibility requirement?', ['Stated reason', 'Relevant conduct and policy context']),
    ],
    boundaries: ['Preserve supported conduct-based accountability and political criticism.', 'Documentation or rejection of derogatory language requires careful attribution.'],
  },
  group_conspiracy: {
    questions: [
      question('mechanism', 'Identify the alleged mechanism', 'Which actors are alleged to coordinate which actions, and how broad is the group claim?', ['Specific actors and actions', 'Exact allegation and group scope']),
      question('corroboration', 'Inspect the evidential basis', 'What evidence establishes the alleged coordination, and what remains an attributed allegation?', ['Corroborating primary evidence', 'Attribution and countervailing evidence']),
    ],
    boundaries: ['Documented secret coordination by named actors remains assessable on its evidence.', 'An identity label or political allegation alone leaves the mechanism unresolved.'],
  },
  evidential_overclaim: {
    questions: [
      question('claim', 'State the exact claim', 'Which proposition about causality, population, magnitude or certainty is being asserted?', ['Reviewer-selected claim', 'Speaker and outcome']),
      question('design', 'Connect claim and design', 'Which method, comparison and result support this exact proposition?', ['Relevant Methods and Results passages', 'Population, outcome and identification assumptions']),
      question('qualification', 'Read the countervailing context', 'Do qualifications, limitations, citations or contrary results alter the claim’s supported scope?', ['Limitations and uncertainty', 'Cited source for the same proposition']),
    ],
    boundaries: ['A figure caption or a description of previous research may trigger a false alarm.', 'A citation marker locates a reference; its presence leaves the reference’s support to verify.'],
  },
  sampling_risk: {
    questions: [question('sampling', 'Trace the sampling process', 'Who entered or was excluded, and could inclusion depend on the exposure or outcome?', ['Recruitment and exclusion rules', 'Target population', 'Selection sensitivity analysis'])],
    boundaries: ['A mention may disclose or address selection concerns.', 'The impact on a specific estimate requires the study design and analysis.'],
  },
  measurement_risk: {
    questions: [question('measurement', 'Connect measure and construct', 'What do the labels or measurements represent, and how were they validated?', ['Construct definition and codebook', 'Validation or disagreement records', 'Consequences of plausible error'])],
    boundaries: ['A reliability statistic leaves construct validity to examine.', 'The passage may describe an earlier study or a safeguard.'],
  },
  confounding: {
    questions: [question('identification', 'Inspect the identification strategy', 'Could shared causes, reverse direction or comparison differences explain the reported association?', ['Temporal order', 'Assignment or exposure process', 'Adjustment rationale and sensitivity analysis'])],
    boundaries: ['A mention of confounding may describe a recognized limit or a safeguard.', 'Associations and causal effects require different evidence.'],
  },
  selective_reporting: {
    questions: [question('reporting', 'Compare available and planned evidence', 'Which analyses or outcomes were planned, performed and reported?', ['Protocol or registration', 'Reported outcomes and analyses', 'Dated changes and explanations'])],
    boundaries: ['Publication selection concerns the wider evidence base.', 'Declared exploratory analyses can be informative.'],
  },
  attrition: {
    questions: [question('missingness', 'Trace missing observations', 'What is missing, why, and which assumptions address its effect on the estimate?', ['Missingness counts and reasons', 'Group differences', 'Handling and sensitivity analysis'])],
    boundaries: ['Disclosure can identify a limit while leaving its size and direction open.', 'A small count can still matter when missingness is systematic.'],
  },
  generalizability: {
    questions: [question('transport', 'Compare the observed and claimed settings', 'Which population, setting and period does the evidence support, and where is the conclusion extended?', ['Sampling frame and study setting', 'Exact target-population claim', 'Transfer evidence and assumptions'])],
    boundaries: ['A narrowly bounded finding can still be useful.', 'An explicit limit on generalization may demonstrate careful reporting.'],
  },
  disclosed_limitation: {
    questions: [question('limitation', 'Connect the limitation to the claim', 'Which claim does this limitation qualify, and is that scope retained in the summary?', ['Limitation passage', 'Affected claim or conclusion', 'Evidence addressing the limit'])],
    boundaries: ['An explicit limitation may support dismissal of a concern about overclaiming.', 'Its significance depends on the actual design and claimed result.'],
  },
  study_design: {
    questions: [question('study-design', 'Inspect the actual design', 'How do sampling, assignment, comparison and analysis support the stated research question?', ['Methods and analysis', 'Comparison or assignment mechanism', 'Relevant assumptions'])],
    boundaries: ['A design name alone leaves the study’s execution and validity to examine.'],
  },
  sample_size: {
    questions: [question('sample-unit', 'Resolve the counted unit', 'Which people, observations or other units does the count describe, and how did they enter the analysis?', ['Denominator and unit', 'Sample flow and exclusions', 'Relevant outcome population'])],
    boundaries: ['A large count can coexist with selection or measurement limitations.', 'Repeated observations and distinct participants require separate denominators.'],
  },
  citation_marker: {
    questions: [question('citation-support', 'Follow the reference', 'Which proposition does the marker attach to, and what does the cited source establish about it?', ['Complete sentence and marker', 'Referenced work and relevant passage', 'Scope and status of the cited evidence'])],
    boundaries: ['A reference can provide background, a method or disagreement as well as support.', 'Verify the marker’s scope in the physical source if PDF extraction changes its position.'],
  },
  qualification: {
    questions: [question('qualification-scope', 'Resolve the qualification', 'Which proposition is qualified, and what condition or uncertainty is retained?', ['Complete proposition', 'Qualification and its scope', 'Underlying design and uncertainty'])],
    boundaries: ['Qualification can demonstrate careful reporting.', 'A nearby uncertainty term can modify a different proposition or appear in an ordinary nontechnical sense.'],
  },
  causal_language: {
    questions: claimQuestions(),
    boundaries: ['Causal vocabulary may occur in a denial, an attributed statement or a discussion of earlier evidence.', 'The relevant design and result determine the supported inference.'],
  },
  material_omission: {
    questions: [question('missing-reference', 'Establish materiality', 'Which missing fact changes interpretation under the passage’s stated purpose, and which supplied source establishes it?', ['Communication purpose and scope', 'Missing fact and source passage', 'Rationale for the changed interpretation'])],
    boundaries: ['A narrow scope can justify leaving unrelated material out.', 'The required reference packet must establish the missing fact and its relevance.'],
  },
  comparative_double_standard: {
    questions: [question('matched-standard', 'Justify the comparison', 'Which evidence or evaluation standard differs between matched cases, and do relevant factual differences explain it?', ['Both complete cases', 'Explicit standards', 'Comparison conditions and relevant differences'])],
    boundaries: ['Different evidence, duties or circumstances can justify different scrutiny.', 'A comparison requires matched conditions and documented exceptions.'],
  },
};

const defaultGuide: ReadingGuide = {
  questions: [question('source-context', 'Resolve the reading in context', 'What proposition does this passage express, who endorses it, and what evidence resolves the selected criterion?', ['Exact source wording', 'Speaker and target', 'Relevant support and countervailing context'])],
  boundaries: ['Preserve supported political criticism and attribution.', 'Framework-sensitive judgments require a named framework, version and contextual rationale.'],
};

function claimQuestions(): ReadingQuestion[] {
  return [
    question('causal-scope', 'Identify the causal proposition', 'Which cause and outcome are asserted, and is the wording direct, qualified, denied or attributed?', ['Complete sentence', 'Speaker, cause and outcome']),
    question('causal-evidence', 'Read the supporting design', 'What study design and result support the causal reading at the stated scope?', ['Methods and Results', 'Identification assumptions', 'Limitations and cited evidence']),
  ];
}

const claimGuide: ReadingGuide = {
  questions: claimQuestions(),
  boundaries: ['A wording match can describe a denial, qualification or another speaker’s claim.', 'A detected citation leaves the cited work’s support to verify.'],
};

function literalObservations(text: string, pattern: RegExp, kind: SourceObservation['kind'], readingQuestion: string) {
  return Array.from(text.matchAll(pattern)).slice(0, 6).map(match => ({
    kind, text: match[0], start: match.index!, end: match.index! + match[0].length, readingQuestion,
  }));
}

export function explainFinding(finding: Finding, report?: ReportContext): FindingExplanation {
  const source: FindingExplanation['source'] = {
    text: finding.text, context: finding.context ?? '', page: finding.page,
    firstLine: finding.firstLine, lastLine: finding.lastLine, section: finding.section,
  };
  const observations: SourceObservation[] = [];
  const limitations = ['Reading cues locate literal source wording. The source, its attribution and the required evidence determine the interpretation.'];
  const focus = finding.focus;
  const validFocus = focus && [focus.start, focus.end, focus.matchStart, focus.matchEnd].every(Number.isInteger)
    && focus.start >= 0 && focus.end <= finding.text.length
    && focus.start < focus.end && focus.matchStart >= focus.start && focus.matchEnd <= focus.end
    && focus.matchStart < focus.matchEnd;
  if (validFocus) {
    source.focusedText = finding.text.slice(focus.start, focus.end);
    observations.push({ kind: 'causal_wording', text: finding.text.slice(focus.matchStart, focus.matchEnd),
      start: focus.matchStart, end: focus.matchEnd,
      readingQuestion: 'What cause and outcome does this wording connect, and who endorses the proposition?' });
    const citation = focus.citation;
    if (citation && Number.isInteger(citation.start) && Number.isInteger(citation.end)
        && citation.start >= focus.start && citation.end <= focus.end && citation.start < citation.end) {
      observations.push({ kind: 'citation_marker', text: finding.text.slice(citation.start, citation.end),
        start: citation.start, end: citation.end,
        readingQuestion: 'Which proposition does this marker attach to, and what does the referenced source establish?' });
    }
    if (focus.partial) limitations.push('The retained sentence reaches a passage boundary. Read the full passage and original source to recover the complete claim.');
  } else if (focus) limitations.push('The recorded sentence offsets do not resolve within this excerpt. Use the full preserved passage.');
  if (finding.category === 'cue' && finding.matched) {
    const start = finding.text.indexOf(finding.matched);
    if (start >= 0) observations.push({ kind: 'recorded_cue', text: finding.matched, start,
      end: start + finding.matched.length,
      readingQuestion: 'Does this mention describe a finding, limitation, safeguard or earlier work?' });
    else limitations.push('The recorded cue has no exact match in this excerpt. Its location requires review.');
  }
  observations.push(...literalObservations(finding.text, /“[^”]+”|"[^"]+"|‘[^’]+’/gu, 'quotation',
    'Who speaks inside this quotation, and does the surrounding passage endorse, report or reject it?'));
  observations.push(...literalObservations(finding.text, /\b(?:according to|said|says|reported|reports|claimed|claims|argued|argues|alleged|alleges)\b/giu, 'reporting_wording',
    'Which speaker and proposition are linked by this wording?'));
  if (['collective_blame', 'identity_stereotyping', 'evidential_overclaim'].includes(finding.check)) {
    observations.push(...literalObservations(finding.text, /\b(?:all|every|entire|always|never|proves?|certainly|may|might|could|associated with)\b/giu, 'scope_wording',
      'What population, frequency or uncertainty does this wording actually modify?'));
  }
  const guide = finding.category === 'claim' ? claimGuide : guides[finding.check] ?? defaultGuide;
  let basis: FindingExplanation['basis'];
  if (finding.category === 'model') {
    basis = { kind: 'model_flag', learnedEvidenceSpan: false,
      description: 'The supplied record contains a passage-level model flag for this criterion. A class score supplies no learned evidence span or source-grounded rationale.' };
    if (Number.isFinite(finding.score)) {
      basis.modelScore = finding.score;
      basis.modelScoreStatus = 'uncalibrated_class_score';
    }
  } else if (finding.category === 'cue') {
    basis = { kind: 'recorded_text_cue', learnedEvidenceSpan: false,
      description: 'A wording pattern recorded a research cue in this passage. The meaning and significance of that mention require contextual reading.' };
  } else {
    basis = { kind: 'causal_reading_rule', learnedEvidenceSpan: false,
      description: 'A documented sentence rule located causal wording and nearby surface context. Its category is a reading prompt.' };
  }
  if (report && !report.complete) limitations.push('The source scan is incomplete. Coverage may exclude relevant support or countervailing context.');
  if (report?.source === 'pdf') limitations.push('PDF extraction can affect reading order, line wraps and table context. Check the physical source page.');
  if (report?.supportLimitReached) limitations.push('The Methods and Results evidence index reached its size limit. The retained index may omit relevant passages.');
  return {
    method: 'doxa-source-reading-v1', source, basis, observations,
    roles: {
      speaker: { status: 'requires_review', question: 'Which author or quoted speaker endorses this proposition, and which source words establish that stance?' },
      target: { status: 'requires_review', question: 'Which actor, population, institution or policy is discussed, and what is the exact scope of that reference?' },
    },
    questions: guide.questions.map(item => ({ ...item, evidenceNeeded: [...item.evidenceNeeded] })),
    boundaries: [...guide.boundaries], limitations,
  };
}

export type MitigationCheck = {
  id: 'numbers' | 'name_like_terms' | 'negation' | 'quotation' | 'uncertainty';
  label: string;
  status: 'changed' | 'no_change_detected' | 'not_assessed';
  original: string[];
  proposed: string[];
  question: string;
};

export type MitigationReading = {
  method: 'doxa-meaning-check-v1';
  status: 'review_required';
  hasDraft: boolean;
  originalText: string;
  proposedText: string;
  checks: MitigationCheck[];
  questions: string[];
  limitations: string[];
};

const nameStopwords = new Set(['The', 'A', 'An', 'This', 'These', 'Those', 'That', 'We', 'Our', 'I', 'It', 'Its', 'They', 'Their', 'He', 'She', 'His', 'Her', 'As', 'In', 'For', 'On', 'At', 'By', 'From', 'To', 'If', 'But', 'And', 'Or', 'No', 'Not', 'May', 'Methods', 'Results', 'Discussion', 'Conclusion']);

function matchedValues(text: string, pattern: RegExp) {
  return Array.from(text.matchAll(pattern), match => match[0]);
}

function nameLikeTerms(text: string) {
  return matchedValues(text, /(?<![\p{L}\p{N}])\p{Lu}[\p{L}\p{N}’'-]*(?:[ \t]+(?:(?:of|the|and)[ \t]+)?\p{Lu}[\p{L}\p{N}’'-]*)*/gu)
    .filter(term => !nameStopwords.has(term));
}

function signature(values: string[]) {
  return values.map(value => value.toLocaleLowerCase().replaceAll('’', "'").replace(/\s+/gu, ' ').trim())
    .sort().join('\u0000');
}

export function checkMitigation(original: string, proposed: string): MitigationReading {
  const hasDraft = Boolean(proposed.trim());
  const checks: MitigationCheck[] = [];
  const compare = (id: MitigationCheck['id'], label: string, before: string[], after: string[], question: string) => {
    let status: MitigationCheck['status'] = 'not_assessed';
    if (hasDraft) status = signature(before) === signature(after) ? 'no_change_detected' : 'changed';
    checks.push({ id, label, status, original: before, proposed: after, question });
  };
  const numbers = /(?:[−-]?\d+(?:[.,]\d+)*(?:\s*%|\s*percent\b)?)/giu;
  const negation = /\b(?:no|not|never|neither|nor|cannot|can['’]t|couldn['’]t|didn['’]t|doesn['’]t|don['’]t|isn['’]t|aren['’]t|wasn['’]t|weren['’]t|without)\b/giu;
  const quotations = /“[^”]+”|"[^"]+"|‘[^’]+’/gu;
  const uncertainty = /\b(?:may|might|could|possibly|probably|likely|unlikely|uncertain|uncertainty|approximately|estimated|suggests?|associated with|proves?|proven|definitely|certainly)\b/giu;
  compare('numbers', 'Numbers and percentage wording', matchedValues(original, numbers), matchedValues(proposed, numbers),
    'Do quantities, denominators, units, dates and comparisons retain their original supported meaning?');
  compare('name_like_terms', 'Name-like capitalized terms', nameLikeTerms(original), nameLikeTerms(proposed),
    'Do the responsible actors, named institutions and target populations retain their actual roles?');
  compare('negation', 'Negation wording', matchedValues(original, negation), matchedValues(proposed, negation),
    'Does each denial or exception still modify the same proposition?');
  compare('quotation', 'Quoted wording', matchedValues(original, quotations), matchedValues(proposed, quotations),
    'Are quotation boundaries, exact quoted words and speaker attribution preserved or explicitly revised?');
  compare('uncertainty', 'Uncertainty and certainty wording', matchedValues(original, uncertainty), matchedValues(proposed, uncertainty),
    'Does the revision preserve the degree and scope of certainty supported by the evidence?');
  return {
    method: 'doxa-meaning-check-v1', status: 'review_required', hasDraft,
    originalText: original, proposedText: proposed, checks,
    questions: [
      'Does the revision preserve the supported political position, criticism and responsible actor?',
      'Which factual proposition or inference changed, and what source warrants that change?',
      'Could changes to scope, conditions, comparison or attribution alter the meaning even when these terms stay the same?',
    ],
    limitations: [
      'These are literal change checks for reviewer attention. Identical extracted terms can still express different propositions.',
      'Name-like terms are capitalization candidates. Lowercase names, roles, coreference and many languages require manual reading.',
      'Quotation and uncertainty patterns can miss nested, implicit or unusual expressions. Dates and ordinary words can also match.',
      'A changed model score or unchanged checklist does not establish that a revision preserves meaning or improves the text.',
    ],
  };
}
