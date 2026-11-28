import type { ClaimResult } from './claim-contract';
import type { DocumentReport, Finding } from './document-audit';
import type { ResearchOrigin } from './research-sources';
import type { FindingAnnotations } from './finding-reading';
import { checkMitigation, explainFinding } from './finding-explanation';
import type { WordingResult } from './context-wording';

export const dispositions = {
  confirmed: 'Concern confirmed',
  dismissed: 'Concern dismissed',
  needs_context: 'Needs follow-up',
};

export const preservationChecks = {
  scope: 'Population, scope and responsible actor preserved',
  position: 'Supported political criticism and policy position preserved',
  evidence: 'Evidence, uncertainty and attribution preserved',
};

export type MitigationDraft = {
  status: 'reviewer_draft';
  proposedText: string;
  purpose: string;
  checks: Record<keyof typeof preservationChecks, boolean>;
};

export type Decision = {
  document: number;
  finding: Finding;
  disposition: keyof typeof dispositions;
  note: string;
  recordedAt: string;
  mitigation?: MitigationDraft;
  annotations?: FindingAnnotations;
};

type CaseSource = { file: File; author: string; doi?: string; report: DocumentReport; researchSource?: ResearchOrigin; metadata?:Record<string,string> };
type CaseFinding = { decision: Decision; title: string; location: string; check?: ClaimResult; wording?:WordingResult };

export function download(content: string, mime: string, filename: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function caseFile(sources: CaseSource[], findings: CaseFinding[], failures: string[], canceled: boolean, methods: string) {
  const packet = document.implementation.createHTMLDocument('DOXA evidence case file');
  const create = <K extends keyof HTMLElementTagNameMap>(name: K, value?: string) => {
    const node = packet.createElement(name);
    if (value !== undefined) node.textContent = value;
    return node;
  };
  const encoding = create('meta');
  encoding.setAttribute('charset', 'utf-8');
  const style = create('style', `body{max-width:52rem;margin:3rem auto;padding:0 1rem;background:#f6f4e9;color:#172b22;font:16px/1.55 system-ui}h1,h2,h3{font-family:monospace;line-height:1.2}section{border-top:1px solid #a0ae9d;margin-top:1.5rem;padding-top:.6rem}blockquote{background:white;margin:.7rem 0;padding:.8rem;border-left:3px solid #216142;font:1rem/1.6 Georgia;white-space:pre-wrap}p,blockquote,li{overflow-wrap:anywhere}a{color:#216142}.note{white-space:pre-wrap}@media print{body{background:white}blockquote{break-inside:avoid}}`);
  packet.documentElement.lang = 'en';
  packet.head.prepend(encoding);
  packet.head.append(style);
  const methodLink = create('a', 'Audit methods and model provenance');
  methodLink.href = methods;
  packet.body.append(create('h1', 'DOXA evidence case file'),
    create('p', `Exported ${new Date().toISOString()} · ${findings.length} review decisions`), methodLink,
    create('p', 'The case file records decisions made in this session on selected excerpts. Screening counts and retained examples stay in the separate JSON audit.'));
  const manifest = create('section');
  manifest.append(create('h2', 'Source coverage'));
  if (canceled) manifest.append(create('p', 'The audit was canceled. Coverage includes the returned documents only.'));
  if (failures.length) manifest.append(create('p', `Failed or partial sources — ${failures.join(', ')}`));
  const decidedSources = new Set(findings.map(item => item.decision.document));
  for (const [index, source] of sources.entries()) {
    const { file, author, doi, report, researchSource } = source;
    const { progress } = report;
    const pages = progress.totalPages === null ? '' : ` · ${progress.pages}/${progress.totalPages} PDF pages visited · ${progress.emptyPages} pages without selectable text · ${progress.extractionErrors} page errors`;
    manifest.append(create('h3', `Source ${index + 1} · ${file.name}`),
      create('p', `${report.complete ? 'Complete' : 'Partial'} · ${progress.screened} of ${progress.passages} processed passages screened${pages}`),
      create('p', `${author ? `Supplied author label — ${author} · ` : ''}Screening model revision — ${report.modelRevision}`));
    if (doi) manifest.append(create('p', `Source DOI — ${doi}`));
    if (source.metadata) manifest.append(create('p', 'Declared comparison context · '+Object.entries(source.metadata).map(([key,value])=>key+' '+value).join(' · ')));
    if (report.sourceQuality) manifest.append(create('p', `PDF reading records · ${report.sourceQuality.length} pages · ${report.sourceQuality.filter(page=>page.source==='ocr').length} recognized by OCR · ${report.sourceQuality.filter(page=>page.issues.length).length} with reading checks`));
    if (researchSource) {
      const original=create('a',researchSource.title);
      original.href=researchSource.sourceUrl;
      manifest.append(create('p','Selected research excerpt. Read the full article for context.'),original,
        create('p',`${researchSource.documentId} · ${researchSource.passageId} · ${researchSource.license.identifier}`),
        create('p',`${researchSource.sourceXPath} · text SHA-256 ${researchSource.textSha256}`));
    }
    if (decidedSources.has(index)) {
      const fingerprint = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()));
      manifest.append(create('p', `Source SHA-256 — ${Array.from(fingerprint, byte => byte.toString(16).padStart(2, '0')).join('')}`));
    }
  }
  packet.body.append(manifest);
  for (const { decision, title, location, check, wording } of findings) {
    const { document: index, finding, disposition, note, recordedAt } = decision;
    const section = create('section');
    const origin = { model: 'Model flag', cue: 'Research wording cue', claim: 'Causal sentence match' }[finding.category];
    section.append(create('h2', dispositions[disposition]),
      create('p', `Source ${index + 1} · ${sources[index].file.name} · ${location} · ${finding.section}`),
      create('p', `${origin} · ${title}`));
    if (finding.focus) section.append(create('h3', 'Selected sentence'),
      create('blockquote', finding.text.slice(finding.focus.start, finding.focus.end)));
    section.append(create('h3', 'Source passage'), create('blockquote', finding.text));
    if (finding.context) section.append(create('h3', 'Extracted surrounding text'), create('blockquote', finding.context));
    const explanation=explainFinding(finding);
    section.append(create('h3','Basis and reading questions'),create('p',explanation.basis.description));
    for (const item of explanation.questions) section.append(create('p',item.question));
    if (decision.annotations) {
      const annotations=decision.annotations;
      section.append(create('h3','Reviewer-entered attribution and evidence'),
        create('p','Speaker · '+(annotations.speaker || 'Unspecified')),
        create('p','Target · '+(annotations.target || 'Unspecified')),create('p',annotations.scopeNote));
      for (const span of annotations.evidence) section.append(create('p',`${span.role} · source UTF-16 offsets ${span.start}–${span.end}`),create('blockquote',span.text));
    }
    if (wording) section.append(create('h3','Contextual wording model'),
      create('p',`${wording.decision.replaceAll('_',' ')} · model score ${wording.model_score ?? 'unresolved'} · ${wording.score_calibration}`),
      create('p',wording.scope),create('p',`${wording.model_id}@${wording.model_revision}`));
    if (check) {
      section.append(create('h3', 'Automated Methods and Results check'),
        create('p', `${check.verdict.replaceAll('_', ' ')} · ${check.screened} checked · ${check.method}`));
      for (const { window } of check.leads) {
        section.append(create('p', `${window.page ? `PDF page ${window.page} · ` : ''}Lines ${window.firstLine}–${window.lastLine}`),
          create('blockquote', window.text));
      }
      if (check.studyPacket) {
        section.append(create('h3','Study context retrieved for review'));
        for (const context of check.studyPacket.sections) {
          section.append(create('p',`${context.section} · ${context.window.page ? `PDF page ${context.window.page} · ` : ''}Lines ${context.window.firstLine}–${context.window.lastLine}`),
            create('blockquote',context.window.text));
        }
        if (check.studyPacket.unavailable.length) section.append(create('p','No separate indexed passage in '+check.studyPacket.unavailable.join(', ')+'.'));
      }
    }
    const reviewerNote = create('p', note || 'No note entered.');
    reviewerNote.className = 'note';
    section.append(create('h3', 'Reviewer note'), reviewerNote, create('p', `Decision recorded ${recordedAt}`));
    if (decision.mitigation) {
      const { proposedText, purpose, checks } = decision.mitigation;
      section.append(create('h3', 'Mitigation draft · in development'),
        create('p', 'Reviewer-authored wording awaiting assessment. The source passage above is preserved.'),
        create('blockquote', proposedText), create('p', purpose || 'No revision purpose entered.'));
      const list = create('ul');
      for (const [key, text] of Object.entries(preservationChecks)) {
        list.append(create('li', `${checks[key as keyof typeof checks] ? 'Reviewer checked' : 'Unchecked'} · ${text}`));
      }
      section.append(list);
      const original=finding.focus ? finding.text.slice(finding.focus.start,finding.focus.end) : finding.text;
      const meaning=checkMitigation(original,proposedText);
      section.append(create('h3','Automated meaning checks · review required'));
      for (const change of meaning.checks) section.append(create('p',`${change.label} · ${change.status.replaceAll('_',' ')} · ${change.question}`));
    }
    packet.body.append(section);
  }
  return '<!doctype html>\n' + packet.documentElement.outerHTML;
}
