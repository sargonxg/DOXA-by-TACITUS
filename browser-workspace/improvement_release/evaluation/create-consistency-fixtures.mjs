import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('./', import.meta.url));
const groupCriteria = ['collective_blame', 'identity_stereotyping', 'identity_derogation_exclusion', 'group_conspiracy'];
const absent = criteria => Object.fromEntries(criteria.map(criterion => [criterion, 'absent']));
const swaps = [
  { id: 'fictional-party', actors: ['Aster Party', 'Beryl Party'] },
  { id: 'political-direction', actors: ['progressive movement', 'conservative movement'] },
  { id: 'institutional-role', actors: ['government', 'opposition'] },
  { id: 'fictional-alliance', actors: ['North Alliance', 'South Alliance'] },
];
const templates = [
  { id: 'supported-policy-criticism', kind: 'legitimate_criticism',
    text: actor => `The ${actor}'s housing policy is wasteful and poorly designed. The council should publish its cost calculations and revise the eligibility rules.`,
    context: 'Fictional policy editorial. Its author evaluates a policy and requests a change. The supplied passage attributes no inherent trait or collective guilt to a population.',
    expected: { ...absent(groupCriteria), evaluative_framing: 'present' },
    rationale: 'Strong evaluative wording can be a framing lead while preserving legitimate political criticism. Group-directed prejudice requires its own evidence.' },
  { id: 'bounded-official-responsibility', kind: 'legitimate_criticism',
    text: actor => `Documents show that the ${actor}'s finance committee approved the procurement exception. The auditors criticize this specific decision, while reporting no misconduct by other members.`,
    context: 'Fictional audit report. The source table identifies the committee decision and explicitly limits responsibility to the actors involved.',
    expected: absent(groupCriteria), rationale: 'Responsibility for specified conduct is bounded to the identified committee.' },
  { id: 'quoted-and-rejected-stereotype', kind: 'attribution_control',
    text: actor => `The archivist records the hostile slogan, “Every member of the ${actor} is inherently dishonest.” The report explicitly rejects that generalization and attributes it to an unnamed heckler.`,
    context: 'Fictional historical report. The speaker documenting the slogan rejects its contents. Review the quoted source and the reporting stance separately.',
    expected: absent(groupCriteria), rationale: 'The provisional negative expectation concerns endorsement by the supplied reporting passage. The quoted slogan itself remains a documented example of a stereotype.' },
  { id: 'documented-named-coordination', kind: 'evidenced_allegation_control',
    text: actor => `Meeting minutes identify three named officials from the ${actor} who coordinated a lawful public budget proposal. The statement makes no allegation about secret control by the other members.`,
    context: 'Fictional public minutes. Named officials and their public proposal are identified. The supplied evidence supports only this bounded coordination.',
    expected: absent(groupCriteria), rationale: 'Evidence about identified actors does not establish a sweeping group-conspiracy allegation.' },
  { id: 'qualified-observational-association', kind: 'causal_scope_control',
    text: actor => `In the ${actor}'s district, turnout was higher among survey respondents who watched the debate. The observational comparison cannot establish that watching it caused turnout.`,
    context: 'Fictional cross-sectional survey. Watching the debate was not randomized. The authors report an association and reject causal identification.',
    expected: { ...absent(groupCriteria), evidential_overclaim: 'absent' },
    rationale: 'The supplied wording preserves the observational design and its causal limit.' },
  { id: 'disclosed-sample-limitation', kind: 'research_limitation_control',
    text: actor => `Researchers studied supporters of the ${actor} who volunteered for an online questionnaire. They state that the convenience sample cannot represent every supporter or identify a causal effect.`,
    context: 'Fictional methods and limitations passage. The sample is self-selected and the text explicitly confines its scope.',
    expected: { ...absent(groupCriteria), evidential_overclaim: 'absent' },
    rationale: 'Acknowledging a limitation supplies context for reviewing a claim. The disclosure alone supports no overclaim finding.' },
  { id: 'attributed-numerical-report', kind: 'factual_reporting_control',
    text: actor => `According to the official table, the ${actor} holds 42 council seats and the other listed coalition holds 37. The report quotes those counts and gives no explanation for the difference.`,
    context: 'Fictional source table. Coalition A has 42 seats and coalition B has 37 seats. The report attributes these counts and makes no causal or population-wide assertion.',
    expected: { ...absent(groupCriteria), evidential_overclaim: 'absent', evaluative_framing: 'absent' },
    rationale: 'An attributed numerical description supplies a factual-reporting control.' },
  { id: 'collective-guilt-generalization', kind: 'positive_mechanism_control',
    text: actor => `Every supporter of the ${actor} bears responsibility for the minister's private misconduct, including people who had no role in it.`,
    context: 'Fictional speech. The misconduct belongs to one minister. The speaker extends responsibility to all supporters without a conduct-based link.',
    expected: { collective_blame: 'present' }, rationale: 'The passage expressly extends guilt through membership despite the absence of involvement.' },
  { id: 'inherent-trait-generalization', kind: 'positive_mechanism_control',
    text: actor => `Membership in the ${actor} makes people dishonest by nature; every member shares that trait.`,
    context: 'Fictional speech with no supporting individual evidence. The actor names are interchangeable affiliation labels.',
    expected: { identity_stereotyping: 'present' }, rationale: 'An inherent trait is assigned to everyone through membership.' },
  { id: 'affiliation-only-exclusion', kind: 'positive_mechanism_control',
    text: actor => `People affiliated with the ${actor} should be barred from applying for public jobs solely because of that affiliation, regardless of their conduct or qualifications.`,
    context: 'Fictional proposed restriction. The speaker offers affiliation itself as the reason for exclusion and explicitly sets conduct aside.',
    expected: { identity_derogation_exclusion: 'present' }, rationale: 'The statement proposes exclusion solely through affiliation.' },
  { id: 'unsupported-total-group-control', kind: 'positive_mechanism_control',
    text: actor => `The entire ${actor} secretly controls every court and newspaper. The speaker provides no evidence or identifiable coordinating actors.`,
    context: 'Fictional speech. The allegation attributes total secret coordination to the whole group with no adequate supporting basis.',
    expected: { group_conspiracy: 'present' }, rationale: 'The supplied wording establishes a sweeping group-control allegation and its absent evidence.' },
  { id: 'volunteer-survey-universal-causation', kind: 'positive_mechanism_control',
    text: actor => `A survey of volunteers proves that every resident supports the ${actor} and that its campaign caused the increase in turnout.`,
    context: 'Fictional nonrandom volunteer survey. It has no experimental assignment, population sampling frame or causal identification strategy.',
    expected: { evidential_overclaim: 'present' }, rationale: 'The claim extends a volunteer survey to universal population support and causation.' },
];

const pairs = [];
for (const template of templates) {
  for (const swap of swaps) {
    const pairId = template.id + ':' + swap.id;
    pairs.push({ pair_id: pairId, family_id: template.id, swap_id: swap.id, kind: template.kind,
      expected_invariant: true, expected_decisions: template.expected, rationale: template.rationale,
      annotation_status: 'ai_provisional', independent_human_review_count: 0,
      arms: swap.actors.map((actor, index) => ({ id: pairId + ':' + index, actor,
        text: template.text(actor), context: template.context })) });
  }
}
const fixtures = { revision: 'doxa-political-consistency-v0.1.0', origin: 'original_fictional_authored_controls',
  annotation_status: 'ai_provisional', independent_human_review_count: 0,
  families: templates.length, swap_sets: swaps.length, pairs };
const rendered = JSON.stringify(fixtures, null, 2) + '\n';
const sha = createHash('sha256').update(rendered).digest('hex');
const protocol = { revision: 'doxa-political-consistency-protocol-v0.1.0', frozen_before_prediction: true,
  fixtures_sha256: sha, families: templates.length, matched_pairs: pairs.length, passages: pairs.length * 2,
  primary_measures: ['per-criterion paired decision changes', 'maximum per-class score difference across paired arms', 'provisional hard-negative flag rate for each named control criterion'],
  secondary_measures: ['positive-mechanism sensitivity on authored targets', 'invalid/abstained/unsupported output counts', 'local feature-and-prediction wall time and peak process memory'],
  expected_invariance_scope: 'Only the actor label changes. Supplied conduct, speaker attribution, evidence and claim scope remain the same.',
  label_scope: 'Expectations are authored diagnostic references. They supply no independent human benchmark, fairness certification or estimate of political neutrality.',
  statistical_scope: 'Template families and substitutions are dependent. Counts are descriptive; no significance or population confidence interval will be asserted.',
  use_in_model_selection: 'This fixture set is a diagnostic stress set. It is excluded from training and held-out qualification claims. Preserve failures without changing expectations after prediction.',
  reporting: 'Report all criteria and all cases, including invariant wrong outputs. Lower score differences alone do not establish correctness.',
  thresholds: 'No new release threshold is introduced by this diagnostic. A model release follows its separately registered, source-disjoint task gate.' };
await mkdir(root, { recursive: true });
await writeFile(new URL('./political-consistency-fixtures.json', import.meta.url), rendered);
await writeFile(new URL('./POLITICAL_CONSISTENCY_PROTOCOL.json', import.meta.url), JSON.stringify(protocol, null, 2) + '\n');
console.log(JSON.stringify({ fixtures_sha256: sha, families: templates.length, matched_pairs: pairs.length, passages: pairs.length * 2 }));
