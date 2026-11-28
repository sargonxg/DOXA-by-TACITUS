type Packet = {
  case_id: string; document_group: string; source_date: string; source_locator: string; topic: string;
  source_url: string; passage: string; passage_words: number; interpretation_note: string;
};
type Judgment = {
  case_id: string; judgment_id: string; claim: string; reference_decision: string;
  evidence_start: number | null; evidence_end: number | null; evidence_quote: string | null; rationale: string;
};
type Desk = {
  counts: Record<string, Record<string, number>>; archive_dates: [string, string];
  event_rows: number; archive_count: number; events_bytes: number; cases: Packet[]; judgments: Judgment[];
};
type Experiment = { model: string; metrics: { judgments: number; macro_f1: number }; constant_support_control: { macro_f1: number } };

const node = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const number = (value: number) => value.toLocaleString('en-US');
const day = (value: string) => `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}`;
const labels: Record<string,string> = {
  verbal_cooperation: 'Verbal cooperation',
  material_cooperation: 'Material cooperation',
  verbal_conflict: 'Verbal conflict',
  material_conflict: 'Material conflict',
  unclassified: 'Unclassified',
};
const verdicts: Record<string,string> = {
  supported: 'Supported by this excerpt',
  contradicted: 'Contradicted by this excerpt',
  insufficient_context: 'Needs additional evidence',
};
let loading: Promise<void> | undefined;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, text: string, className?: string) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function render(desk: Desk) {
  const months = Object.keys(desk.counts).sort();
  const monthPicker = node<HTMLSelectElement>('archive-month');
  monthPicker.replaceChildren(new Option('All archive months',''),
    ...months.map(key => new Option(`${key.slice(0,4)}-${key.slice(4)}`,key)));
  node('archive-summary').textContent =
    `${number(desk.event_rows)} coded records from ${number(desk.archive_count)} daily archives, `
    + `${day(desk.archive_dates[0])} through ${day(desk.archive_dates[1])}. `
    + `${number(Math.round(desk.events_bytes / 1_000_000))} MB of event Parquet files.`;
  function renderCounts() {
    const dates = monthPicker.value ? [monthPicker.value] : months;
    const totals = Object.fromEntries(Object.keys(labels).map(key => [key, 0]));
    for (const month of dates) {
      for (const [category,count] of Object.entries(desk.counts[month])) totals[category] += count;
    }
    const sum = Object.values(totals).reduce((a,b) => a + b, 0);
    node('archive-counts').replaceChildren(
      element('p',`${number(sum)} coded records in this selection.`),
      ...Object.entries(totals).filter(([key,count]) => count || key !== 'unclassified').map(([key,count]) => {
        const row = element('div','', 'archive-meter');
        const share = sum ? 100 * count / sum : 0;
        const barCount = Math.round(share * 12 / 100);
        const bar = element('span', '■'.repeat(barCount) + '·'.repeat(12-barCount), 'archive-marks');
        bar.setAttribute('aria-hidden','true');
        row.append(element('span', `${labels[key]} · ${number(count)} · ${share.toFixed(1)}%`), bar);
        return row;
      }),
    );
  }
  monthPicker.onchange = renderCounts;
  renderCounts();
  const packet = node<HTMLSelectElement>('archive-packet');
  const claim = node<HTMLSelectElement>('archive-claim');
  packet.replaceChildren(...desk.cases.map(caseRow => new Option(
    `Public Law ${caseRow.document_group} · ${caseRow.topic}`, caseRow.case_id)));
  function renderCase() {
    const source = desk.cases.find(row => row.case_id === packet.value);
    const annotation = desk.judgments.find(row => row.judgment_id === claim.value);
    if (!source || !annotation) return;
    const text = document.createElement('blockquote');
    text.className = 'archive-passage';
    if (annotation.evidence_start !== null && annotation.evidence_end !== null && annotation.evidence_quote) {
      const points = [...source.passage];
      const snippet = points.slice(annotation.evidence_start, annotation.evidence_end).join('');
      if (snippet === annotation.evidence_quote) {
        text.append(points.slice(0, annotation.evidence_start).join(''), element('mark',snippet),
          points.slice(annotation.evidence_end).join(''));
      } else {
        text.textContent = source.passage;
      }
    } else {
      text.textContent = source.passage;
    }
    const citation = element('a',`Read the original on GovInfo · ${source.source_locator}`);
    citation.href = source.source_url;
    node<HTMLButtonElement>('download-model-input').onclick = () => {
      const input = { passage: source.passage, claim: annotation.claim };
      const url = URL.createObjectURL(new Blob([JSON.stringify(input, null, 2) + '\n'], {type: 'application/json'}));
      const link = element('a', '');
      link.href = url;
      link.download = 'doxa-model-input.json';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    node('archive-evidence').replaceChildren(
      element('h3',verdicts[annotation.reference_decision]),
      element('p',annotation.rationale),
      element('p',`AI-provisional reference · ${source.passage_words} words · Enacted ${source.source_date}`,'small'),
      text,citation,
    );
  }
  function updateClaims() {
    claim.replaceChildren(...desk.judgments.filter(row => row.case_id === packet.value)
      .map(row => new Option(row.claim, row.judgment_id)));
    renderCase();
  }
  packet.onchange = updateClaims;
  claim.onchange = renderCase;
  updateClaims();
}

export function openConflictDesk() {
  if (loading) return;
  node('conflict-status').textContent = 'Loading the bundled public summary…';
  loading = (async () => {
    try {
      const response = await fetch(new URL('./generated/conflict-desk.json',document.baseURI));
      if (!response.ok) throw new Error('Missing summary data');
      render(await response.json() as Desk);
      node('conflict-content').hidden = false;
      node('conflict-status').textContent = '';
      void renderExperiment();
    } catch {
      node('conflict-status').textContent = 'The archive summary could not load. Reopen the desk to retry.';
      loading = undefined;
    }
  })();
}

async function renderExperiment() {
  try {
    const response = await fetch(new URL('./generated/contextual-results.json',document.baseURI));
    if (!response.ok) return;
    const experiment = await response.json() as Experiment;
    node('experiment-score').textContent =
      `DeBERTa-v3-small scored ${experiment.metrics.macro_f1.toFixed(3)} macro-F1 versus `
      + `${experiment.constant_support_control.macro_f1.toFixed(3)} for the constant-decision control `
      + `across ${experiment.metrics.judgments} provisional judgments.`;
    node('contextual-experiment').hidden = false;
  } catch {
    // The archive and its references remain available if experiment assets fail.
  }
}
