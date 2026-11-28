import { buildComparisonCohorts, comparisonFields } from './comparison-cohorts';
import type { ComparisonMetadata } from './comparison-cohorts';
import type { DocumentReport } from './document-audit';

type Source = { file: File; metadata?: Record<string, string>; report: DocumentReport;sha256?:string };

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}

export function comparisonDocuments(sources: Source[]) {
  const fingerprints=new Set<string>();
  return sources.flatMap((source, index) => {
    if(source.sha256 && fingerprints.has(source.sha256))return [];
    if(source.sha256)fingerprints.add(source.sha256);
    return [{
    id: String(index), label: source.file.name, metadata: source.metadata ?? {},
    criteria: source.report.criteria, modelRevision: source.report.modelRevision, complete: source.report.complete,
    coverage: { screened: source.report.progress.screened, pages: source.report.progress.pages,
      emptyPages: source.report.progress.emptyPages, extractionErrors: source.report.progress.extractionErrors,
      eligible: Object.values(source.report.sections).reduce((sum, section) => sum + section.eligible, 0) },
  }];});
}

export function renderComparisonEditor(sources: Source[], selected: number,
  apply: (indices: number[], metadata: ComparisonMetadata) => void) {
  const panel = document.getElementById('comparison-setup')!;
  panel.hidden = !sources.length;
  if (!sources[selected]) return;
  const fields = document.getElementById('comparison-fields')!;
  fields.replaceChildren(node('p', sources[selected].file.name));
  const inputs = comparisonFields.map(field => {
    const label = node('label', field[0].toUpperCase() + field.slice(1));
    const input = node('input');
    input.id = 'comparison-' + field;
    input.maxLength = 160;
    input.value = sources[selected].metadata?.[field] ?? '';
    label.htmlFor = input.id;
    fields.append(label, input);
    return { field, input };
  });
  const values = () => Object.fromEntries(inputs.map(({ field, input }) => [field, input.value.trim()]));
  document.getElementById('comparison-save')!.onclick = () => apply([selected], values());
  document.getElementById('comparison-all')!.onclick = () => apply(sources.map((_, index) => index), values());
  const result = buildComparisonCohorts(comparisonDocuments(sources));
  const overview = document.getElementById('comparison-cohorts')!;
  overview.replaceChildren(node('p', `${result.matchedDocumentCount} of ${result.documentCount} documents have a declared matching peer across event, topic, genre and period.`));
  for (const group of result.matchedGroups) {
    overview.append(node('p', `${group.documents.length} documents · ${comparisonFields.map(field => group.metadata[field]).join(' · ')}`));
    for (const caution of group.cautions) overview.append(node('p', caution));
  }
  if (result.unmatched.length) {
    const list = node('ul');
    for (const item of result.unmatched) {
      list.append(node('li', item.label + ' · ' + (item.reason === 'missing_metadata'
        ? 'Missing ' + item.missingFields.join(', ') : 'No peer with the same declared context')));
    }
    overview.append(list);
  }
  overview.append(node('p', 'Shared labels establish the declared comparison set. Review the underlying events, evidence and source-selection rules before interpreting a difference.'));
  return result;
}
