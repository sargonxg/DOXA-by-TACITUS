type Explanation = {
  name: string; kind: string; meaning: string; question: string;
  example?: string; boundary: string; action: string; source: [string, string];
};

const framing: [string, string] = ['Entman, Framing (1993)', 'https://doi.org/10.1111/j.1460-2466.1993.tb01304.x'];
const causal: [string, string] = ['Hernán & Robins, Causal Inference', 'https://miguelhernan.org/whatifbook'];
const methods: [string, string] = ['Cochrane Handbook, nonrandomized studies', 'https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-25'];
const handbook: [string, string] = ['DOXA annotation handbook', 'https://huggingface.co/datasets/Giuliocc/doxa-pilot/blob/v0.1.0/annotation-handbook.md'];

export const explanations: Record<string, Explanation> = {
  evaluative_framing: {
    name: 'Evaluative framing', kind: 'Model screening criterion',
    meaning: 'Specific wording presents an actor, policy or action favorably or unfavorably through evaluation.',
    question: 'Which words carry the evaluation, whose stance is expressed, and what evidence supports it?',
    example: '“The reckless committee imposed another disastrous policy.”',
    boundary: 'Declared advocacy, satire, a clearly attributed quotation or supported criticism can contain evaluative language.',
    action: 'Attribute the evaluation, state the evidence, or retain the judgment as a clearly expressed political position.', source: framing,
  },
  collective_blame: {
    name: 'Collective blame', kind: 'Model screening criterion',
    meaning: 'Responsibility for conduct extends from identified actors to a broader population through group membership.',
    question: 'Who acted, and does the passage hold other people responsible solely through their membership?',
    example: '“Every member of that community is responsible for the minister’s decision.”',
    boundary: 'Criticism of a government, party leadership, institution or documented decision requires assessment of the actual actor and scope.',
    action: 'Name the responsible actors and their conduct, preserving the supported accountability claim.', source: handbook,
  },
  identity_stereotyping: {
    name: 'Identity stereotyping', kind: 'Model screening criterion',
    meaning: 'Traits or behavior are assigned to people through identity membership in a way that exceeds the available evidence.',
    question: 'What sample or conduct supports the generalization, and which people does it include?',
    example: '“People of that religion are inherently dishonest.”',
    boundary: 'A bounded empirical result about a specified sample needs its method, uncertainty and population context.',
    action: 'Describe the observed conduct and its measured scope. Preserve relevant identity context when evidence calls for it.',
    source: ['Fiske et al., stereotype content (2002)', 'https://pubmed.ncbi.nlm.nih.gov/12051578/'],
  },
  identity_derogation_exclusion: {
    name: 'Identity derogation or exclusion', kind: 'Model screening criterion',
    meaning: 'People are degraded, dehumanized or excluded through an identity attributed to them.',
    question: 'Who is targeted, what degradation or exclusion is proposed, and who is speaking?',
    example: '“Members of that faith should be barred from public office.”',
    boundary: 'Quotation for documentation, conduct-based criticism and a relevant eligibility condition need their own contextual reading.',
    action: 'Specify the relevant conduct or eligibility requirement and seek contextual review when identity itself is the reason for exclusion.', source: handbook,
  },
  group_conspiracy: {
    name: 'Group conspiracy', kind: 'Model screening criterion',
    meaning: 'A group is portrayed as secretly coordinating control over institutions or events without an adequate evidential basis, often through sweeping identity claims.',
    question: 'Which specific actors, actions and evidence establish the alleged coordination?',
    example: '“That entire ethnic group secretly controls every election.”',
    boundary: 'Documented coordination, corruption or an evidenced allegation about named officials must be assessed against its sources.',
    action: 'Identify evidenced actors and mechanisms, attribute allegations and request the missing support.',
    source: ['Douglas & Sutton, conspiracy definitions (2023)', 'https://doi.org/10.1146/annurev-psych-032420-031329'],
  },
  evidential_overclaim: {
    name: 'Evidence overclaim', kind: 'Model screening criterion',
    meaning: 'A conclusion exceeds the evidence in causality, population, precision or certainty.',
    question: 'What design, comparison and result support this exact wording?',
    example: '“This observational association proves the policy caused the improvement everywhere.”',
    boundary: 'A qualification, a description of earlier work or a figure caption can trigger a false alarm. Read the full claim and its evidence.',
    action: 'Align the claim with the measured population, comparison, identification assumptions and uncertainty.', source: causal,
  },
  sampling_risk: {
    name: 'Sampling or selection', kind: 'Research issue mentioned in text',
    meaning: 'How entry into a sample or analysis may affect what can be learned about a population or relationship.',
    question: 'Who entered, who was excluded, and could inclusion depend on exposure or outcome?',
    boundary: 'A mention can describe an acknowledged limitation or a safeguard that addresses it.',
    action: 'Inspect the recruitment, inclusion criteria, comparison population and any sensitivity analysis.', source: methods,
  },
  measurement_risk: {
    name: 'Measurement or coding', kind: 'Research issue mentioned in text',
    meaning: 'Whether the recorded labels and measurements represent the intended construct consistently.',
    question: 'What do the labels mean, how were they validated, and where did coders disagree?',
    boundary: 'A reliability statistic alone leaves construct validity and the consequences of disagreement open.',
    action: 'Check the codebook, annotation provenance, measurement validation and plausible alternative labels.', source: methods,
  },
  confounding: {
    name: 'Confounding or endogeneity', kind: 'Research issue mentioned in text',
    meaning: 'An association may reflect common causes, reverse direction or other differences between compared groups.',
    question: 'What causal assumptions connect the design and the estimate?',
    boundary: 'A mention of confounding may explain the study’s own safeguards or limits.',
    action: 'Inspect the comparison, temporal order, covariate choices and identification strategy.', source: causal,
  },
  selective_reporting: {
    name: 'Selective reporting or publication', kind: 'Research issue mentioned in text',
    meaning: 'Available results may depend on which analyses, outcomes or studies were reported.',
    question: 'Which planned analyses and outcomes are available, and what evidence could be missing?',
    boundary: 'Publication bias concerns a wider evidence base. A single paper usually supplies only part of the necessary context.',
    action: 'Compare protocols, registrations, outcomes, supplements and the broader literature.',
    source: ['Cochrane Handbook, missing evidence', 'https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-13'],
  },
  attrition: {
    name: 'Attrition or missingness', kind: 'Research issue mentioned in text',
    meaning: 'Loss of participants, records or measurements may affect an estimate or its interpretation.',
    question: 'What disappeared, why, and how could missingness depend on the outcome or exposure?',
    boundary: 'Missingness can be small, consequential, addressed in analysis or explicitly disclosed.',
    action: 'Inspect missing-data counts, reasons, group differences and sensitivity to alternative assumptions.', source: methods,
  },
  generalizability: {
    name: 'Generalizability', kind: 'Research issue mentioned in text',
    meaning: 'How a finding transfers across populations, settings, institutions or periods.',
    question: 'Which population and setting does the design support, and what changes elsewhere?',
    boundary: 'An explicit limit to generalization can be evidence of careful reporting.',
    action: 'Compare the stated claim with the sampling frame, setting, period and external evidence.',
    source: ['STROBE reporting guidance', 'https://www.strobe-statement.org/'],
  },
  study_design: {
    name: 'Study design', kind: 'Research wording cue',
    meaning: 'A phrase naming an observational, experimental or other study design.',
    question: 'How does this design support the estimate and the claim under review?',
    boundary: 'Design terminology alone establishes neither validity nor a causal effect.',
    action: 'Read the actual sample, comparison, assignment and analysis alongside the design label.', source: causal,
  },
  disclosed_limitation: {
    name: 'Reported limitation', kind: 'Research wording cue',
    meaning: 'The passage mentions a limit, uncertainty or possible threat to the interpretation of a study.',
    question: 'Which claim does this limitation qualify, and is the same scope maintained in the conclusions?',
    boundary: 'An explicit limitation can be evidence of careful reporting. Its significance depends on the actual design and claim.',
    action: 'Read the affected claim, the stated limit and any evidence that addresses it together.', source: methods,
  },
};

let tooltipId = 0;

export function criterionHelp(key: string, text?: string) {
  const info = explanations[key];
  if (!info) return document.createTextNode(text ?? key);
  const wrapper = document.createElement('span');
  wrapper.className = 'criterion-help';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'explain-button';
  button.textContent = text ?? info.name;
  button.setAttribute('aria-label', 'Explain ' + info.name);
  const tip = document.createElement('span');
  tip.className = 'criterion-tooltip';
  tip.id = 'criterion-tip-' + ++tooltipId;
  tip.setAttribute('role', 'tooltip');
  tip.textContent = info.meaning + ' Open for examples and review guidance.';
  button.setAttribute('aria-describedby', tip.id);
  button.onclick = () => openGuide(key);
  const place = () => {
    const box = button.getBoundingClientRect();
    tip.style.left = Math.max(12, Math.min(box.left, innerWidth - 324)) + 'px';
    tip.style.top = (box.bottom < innerHeight - 160 ? box.bottom + 8 : Math.max(12, box.top - 150)) + 'px';
  };
  wrapper.onpointerenter = place;
  wrapper.addEventListener('focusin', place);
  wrapper.append(button, tip);
  return wrapper;
}

export function openGuide(key = '') {
  const dialog = document.querySelector<HTMLDialogElement>('#bias-guide')!;
  if (!dialog.open) dialog.showModal();
  history.replaceState(null, '', '#guide/' + key);
  const target = document.getElementById('guide-' + key);
  if (target) {
    target.scrollIntoView({ block: 'start' });
    target.focus({ preventScroll: true });
  } else dialog.scrollTop = 0;
}

export function mountGuide() {
  const dialog = document.querySelector<HTMLDialogElement>('#bias-guide')!;
  const contents = document.getElementById('guide-contents')!;
  for (const [key, info] of Object.entries(explanations)) {
    const section = document.createElement('section');
    section.id = 'guide-' + key;
    section.tabIndex = -1;
    const title = document.createElement('h3');
    title.textContent = info.name;
    const kind = document.createElement('p');
    kind.className = 'muted';
    kind.textContent = info.kind;
    section.append(title, kind);
    for (const text of [info.meaning, info.question, info.boundary, info.action]) {
      const p = document.createElement('p');
      p.textContent = text;
      section.append(p);
    }
    if (info.example) {
      const p = document.createElement('p');
      p.className = 'guide-example';
      p.textContent = 'Fictional illustration · ' + info.example;
      section.append(p);
    }
    const source = document.createElement('a');
    source.textContent = info.source[0];
    source.href = info.source[1];
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    section.append(source);
    contents.append(section);
  }
  document.getElementById('guide-open')!.onclick = () => openGuide();
  document.getElementById('guide-close')!.onclick = () => dialog.close();
  dialog.addEventListener('close', () => {
    if (location.hash.startsWith('#guide')) history.replaceState(null, '', location.pathname + location.search);
  });
  if (location.hash.startsWith('#guide')) openGuide(location.hash.slice(7));
}
