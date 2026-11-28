import type { CriterionCounts, DocumentReport } from './document-audit';

export const outcomes = [
  ['present', 'Flagged'], ['absent', 'No flag'], ['uncertain', 'Needs context'],
  ['abstained', 'Abstained'], ['unsupported', 'Unsupported'],
] as const;

export function poolCounts(counts: (CriterionCounts | undefined)[]): CriterionCounts {
  const sum: CriterionCounts = { present: 0, absent: 0, uncertain: 0, abstained: 0, unsupported: 0 };
  for (const row of counts) {
    if (row) for (const [key] of outcomes) sum[key] += row[key];
  }
  return sum;
}

export function countSummary(counts: CriterionCounts) {
  const total = outcomes.reduce((sum, [key]) => sum + counts[key], 0);
  const resolved = counts.present + counts.absent;
  const unresolved = counts.uncertain + counts.abstained + counts.unsupported;
  return { total, resolved, unresolved };
}

export function percent(part: number, total: number) {
  return total ? (100 * part / total).toFixed(1) + '%' : '—';
}

export function distribution(counts: CriterionCounts) {
  const { total } = countSummary(counts);
  const bar = document.createElement('div');
  bar.className = 'distribution';
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', total
    ? outcomes.map(([key, text]) => `${counts[key]} ${text.toLowerCase()}`).join(', ') + ` out of ${total} checks`
    : 'No eligible checks');
  bar.title = bar.getAttribute('aria-label')!;
  for (const [key] of outcomes) {
    if (!counts[key]) continue;
    const segment = document.createElement('span');
    segment.className = key;
    segment.style.width = `${100 * counts[key] / total}%`;
    segment.dataset.count = String(counts[key]);
    segment.setAttribute('aria-hidden', 'true');
    bar.append(segment);
  }
  return bar;
}

export type CorpusSource = { file: File; author: string; report: DocumentReport };

export function corpusSummary(sources: CorpusSource[], checks: readonly (readonly [string, string])[]) {
  const totals = checks.map(([key, title]) => {
    const counts = poolCounts(sources.map(source => source.report.criteria[key]));
    return { key, title, counts, ...countSummary(counts) };
  });
  const coverage = sources.reduce((sum, { report }) => {
    sum.screened += report.progress.screened;
    sum.pages += report.progress.pages;
    sum.gaps += report.progress.emptyPages + report.progress.extractionErrors;
    sum.eligible += Object.values(report.sections).reduce((n, section) => n + section.eligible, 0);
    return sum;
  }, { screened: 0, eligible: 0, pages: 0, gaps: 0 });
  const unresolved = [...totals].sort((a, b) => b.unresolved - a.unresolved)[0];
  return { coverage, totals, unresolved };
}
