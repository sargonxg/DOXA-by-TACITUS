import type { Finding } from './document-audit';
import { checkMitigation, explainFinding } from './finding-explanation';

export type EvidenceAnnotation = {
  role: 'supporting' | 'countervailing';
  start: number;
  end: number;
  text: string;
};

export type FindingAnnotations = {
  provenance: 'reviewer_entered';
  speaker: string;
  target: string;
  scopeNote: string;
  evidence: EvidenceAnnotation[];
};

let componentSequence = 0;

function node<K extends keyof HTMLElementTagNameMap>(name: K, text?: string) {
  const element = document.createElement(name);
  if (text !== undefined) element.textContent = text;
  return element;
}

function splitsSurrogatePair(text: string, offset: number) {
  return offset > 0 && offset < text.length
    && /[\uD800-\uDBFF]/.test(text[offset - 1])
    && /[\uDC00-\uDFFF]/.test(text[offset]);
}

function validEvidence(value: EvidenceAnnotation, source: string) {
  return value && ['supporting', 'countervailing'].includes(value.role)
    && Number.isInteger(value.start) && Number.isInteger(value.end)
    && value.start >= 0 && value.start < value.end && value.end <= source.length
    && !splitsSurrogatePair(source, value.start) && !splitsSurrogatePair(source, value.end)
    && source.slice(value.start, value.end) === value.text && Boolean(value.text.trim());
}

// Textarea controls display CRLF and CR as LF. Keep a map from their UTF-16
// selection boundaries back to the preserved source's UTF-16 boundaries.
function selectorText(source: string) {
  let text = '';
  const offsets = [0];
  for (let index = 0; index < source.length; index++) {
    let unit = source[index];
    if (unit === '\r') {
      unit = '\n';
      if (source[index + 1] === '\n') index++;
    }
    text += unit;
    offsets.push(index + 1);
  }
  return { text, offsets };
}

export function createFindingReading(finding: Finding, existingAnnotations?: FindingAnnotations) {
  const id = 'finding-reading-' + ++componentSequence;
  const explanation = explainFinding(finding);
  const element = node('section');
  element.className = 'finding-reading';
  element.dataset.passage = String(finding.passage);
  element.dataset.criterion = finding.check;
  element.dataset.category = finding.category;
  element.setAttribute('aria-label', 'Source reading and reviewer evidence');

  const questions = node('details');
  questions.className = 'reading-questions';
  questions.append(node('summary', 'What to verify in this passage'));
  for (const item of explanation.questions) {
    const question = node('div');
    question.append(node('h4', item.title), node('p', item.question),
      node('p', 'Evidence to inspect · ' + item.evidenceNeeded.join(' · ')));
    questions.append(question);
  }
  const boundaries = node('ul');
  for (const text of explanation.boundaries) boundaries.append(node('li', text));
  questions.append(boundaries);

  const cues = node('details');
  cues.className = 'reading-cues';
  cues.append(node('summary', 'Recorded basis and source cues'), node('p', explanation.basis.description));
  if (explanation.observations.length) {
    const list = node('ul');
    for (const observation of explanation.observations) {
      const item = node('li');
      const quote = node('mark', observation.text);
      quote.dataset.kind = observation.kind;
      quote.dataset.start = String(observation.start);
      quote.dataset.end = String(observation.end);
      item.append(quote, node('p', observation.readingQuestion));
      list.append(item);
    }
    cues.append(list);
  }
  for (const text of explanation.limitations) cues.append(node('p', text));
  questions.append(cues);
  element.append(questions);

  const editor = node('details');
  editor.className = 'evidence-annotations';
  editor.append(node('summary', 'Record speaker and evidence'));
  const instructions = node('p', 'Enter your reading of the speaker and target. Select exact words in the original passage to record supporting or countervailing evidence. Entries are included when you save the review decision.');
  instructions.id = id + '-instructions';
  editor.append(instructions);
  const fields = node('div');
  fields.className = 'reviewer-fields';

  const trustedExisting = existingAnnotations?.provenance === 'reviewer_entered' ? existingAnnotations : undefined;
  const field = (name: 'speaker' | 'target' | 'scopeNote', caption: string) => {
    const input = name === 'scopeNote' ? node('textarea') : node('input');
    input.id = id + '-' + name;
    input.maxLength = name === 'scopeNote' ? 2000 : 300;
    input.value = typeof trustedExisting?.[name] === 'string' ? trustedExisting[name] : '';
    if (input instanceof HTMLTextAreaElement) input.rows = 2;
    else input.type = 'text';
    const label = node('label', caption);
    label.htmlFor = input.id;
    fields.append(label, input);
    return input;
  };
  const speaker = field('speaker', 'Speaker or attributed source (reviewer supplied)');
  const target = field('target', 'Actor, group or policy discussed (reviewer supplied)');
  const scopeNote = field('scopeNote', 'Scope, attribution or context note');
  editor.append(fields);

  const sourceLabel = node('label', 'Original passage (select evidence)');
  const selector = node('textarea');
  selector.className = 'source-selector';
  selector.id = id + '-source';
  selector.readOnly = true;
  selector.spellcheck = false;
  selector.rows = Math.min(10, Math.max(4, finding.text.split('\n').length));
  selector.setAttribute('aria-describedby', instructions.id);
  const display = selectorText(finding.text);
  selector.value = display.text;
  sourceLabel.htmlFor = selector.id;
  editor.append(sourceLabel, selector);

  const actions = node('div');
  actions.className = 'evidence-actions';
  const supporting = node('button', 'Add supporting selection');
  const countervailing = node('button', 'Add countervailing selection');
  supporting.type = countervailing.type = 'button';
  supporting.disabled = countervailing.disabled = true;
  actions.append(supporting, countervailing);
  const status = node('p');
  status.className = 'evidence-status';
  status.setAttribute('role', 'status');
  const list = node('ol');
  list.className = 'recorded-evidence';
  editor.append(actions, status, list);
  element.append(editor);

  const suppliedEvidence = Array.isArray(trustedExisting?.evidence) ? trustedExisting.evidence : [];
  let evidence = suppliedEvidence.filter(item => validEvidence(item, finding.text)).map(item => ({ ...item }));
  if (existingAnnotations && !trustedExisting) status.textContent = 'The prior annotation provenance requires review before entries can be restored.';
  else if (evidence.length !== suppliedEvidence.length) status.textContent = 'Some prior evidence entries do not match this source passage. Check the original saved audit before replacing them.';

  const selection = () => {
    const start = display.offsets[selector.selectionStart];
    const end = display.offsets[selector.selectionEnd];
    return { start, end, text: finding.text.slice(start, end) };
  };
  const updateSelection = () => {
    const selected = selection();
    const valid = selector.value === display.text && validEvidence({ role: 'supporting', ...selected }, finding.text);
    supporting.disabled = countervailing.disabled = !valid;
  };
  selector.addEventListener('select', updateSelection);
  selector.addEventListener('keyup', updateSelection);
  selector.addEventListener('pointerup', updateSelection);
  selector.addEventListener('focus', updateSelection);

  const renderEvidence = () => {
    list.replaceChildren();
    for (const [index, item] of evidence.entries()) {
      const entry = node('li');
      const title = item.role === 'supporting' ? 'Supporting evidence' : 'Countervailing evidence';
      const quote = node('blockquote', item.text);
      const remove = node('button', 'Remove');
      remove.type = 'button';
      remove.className = 'outline';
      remove.setAttribute('aria-label', 'Remove ' + item.role + ' evidence ' + (index + 1));
      remove.onclick = () => {
        evidence = evidence.filter((_, position) => position !== index);
        renderEvidence();
        status.textContent = 'Evidence entry removed from this editor.';
        selector.focus();
      };
      entry.append(node('p', title), quote, remove);
      list.append(entry);
    }
  };
  const addEvidence = (role: EvidenceAnnotation['role']) => {
    const selected = { role, ...selection() };
    if (selector.value !== display.text || !validEvidence(selected, finding.text)) {
      status.textContent = 'Select complete source characters before adding evidence.';
      return;
    }
    if (evidence.some(item => item.role === role && item.start === selected.start && item.end === selected.end)) {
      status.textContent = 'That selection is already recorded with this evidence role.';
      return;
    }
    evidence.push(selected);
    renderEvidence();
    status.textContent = (role === 'supporting' ? 'Supporting' : 'Countervailing') + ' selection recorded in this editor.';
  };
  supporting.onclick = () => addEvidence('supporting');
  countervailing.onclick = () => addEvidence('countervailing');
  renderEvidence();

  return {
    element,
    readAnnotations(): FindingAnnotations | undefined {
      const values = { speaker: speaker.value.trim(), target: target.value.trim(), scopeNote: scopeNote.value.trim() };
      if (!values.speaker && !values.target && !values.scopeNote && !evidence.length) return undefined;
      return { provenance: 'reviewer_entered', ...values, evidence: evidence.map(item => ({ ...item })) };
    },
  };
}

export function renderMitigationReview(original: string, proposed: string): HTMLElement {
  const reading = checkMitigation(original, proposed);
  const element = node('section');
  element.className = 'meaning-review';
  element.dataset.status = reading.status;
  element.setAttribute('aria-label', 'Revision meaning review');
  element.append(node('h4', 'Review the revision'), node('p', 'Reviewer assessment required. Literal change checks direct attention to wording that may alter the meaning.'));
  if (!reading.hasDraft) {
    element.append(node('p', 'Enter proposed wording to inspect its term changes.'));
    return element;
  }
  const changed = reading.checks.filter(item => item.status === 'changed');
  const list = node('div');
  list.className = 'meaning-changes';
  for (const item of changed) {
    const detail = node('details');
    detail.append(node('summary', item.label + ' · change to check'),
      node('p', 'Original terms · ' + (item.original.join(' · ') || 'None detected')),
      node('p', 'Proposed terms · ' + (item.proposed.join(' · ') || 'None detected')),
      node('p', item.question));
    list.append(detail);
  }
  if (!changed.length) list.append(node('p', 'No change was detected in the extracted term lists. Check the propositions, actor roles and political meaning directly.'));
  element.append(list);
  const remaining = reading.checks.filter(item => item.status === 'no_change_detected');
  if (remaining.length) {
    const detail = node('details');
    detail.append(node('summary', 'Review term lists with no detected change'));
    for (const item of remaining) detail.append(node('h5', item.label), node('p', item.question));
    element.append(detail);
  }
  const questions = node('ul');
  for (const text of reading.questions) questions.append(node('li', text));
  element.append(questions, node('p', reading.limitations[0]));
  return element;
}
