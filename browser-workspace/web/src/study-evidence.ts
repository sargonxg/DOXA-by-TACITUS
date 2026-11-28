import { ClaimIndex } from './claim-index';
import type { EvidenceWindow } from './claim-index';
import type { DocumentReport } from './document-audit';

export type StudyPacket = {
  method: 'doxa-study-context-v1';
  claim: string;
  sections: { section: 'methods' | 'results' | 'limitations'; window: EvidenceWindow; selection: 'lexical_candidate' }[];
  unavailable: string[];
  noMatchingPassage: string[];
};

export function separateEvidence<T extends EvidenceWindow & { index: number }>(claim: string, origin: number, passages: T[]) {
  const canonical = (text: string) => text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const signature = canonical(claim);
  const reader = new Intl.Segmenter('en', { granularity: 'sentence' });
  return passages.filter(window => window.index !== origin &&
    !Array.from(reader.segment(window.text.replace(/[\r\n]/g, ' '))).some(({ segment }) => canonical(segment) === signature));
}

export function studyPacket(claim: string, origin: number, report: DocumentReport): StudyPacket {
  const packet: StudyPacket = { method: 'doxa-study-context-v1', claim, sections: [], unavailable: [], noMatchingPassage: [] };
  for (const section of ['methods', 'results', 'limitations'] as const) {
    const pool = section === 'limitations' ? report.limitationPassages ?? [] : report.supportPassages ?? [];
    const available = separateEvidence(claim, origin, pool.filter(window => window.section === section));
    if (!available.length) { packet.unavailable.push(section); continue; }
    const index = new ClaimIndex(claim);
    for (const window of available) index.add(window);
    const match = index.shortlist(1)[0];
    if (match) packet.sections.push({ section, window: match, selection: 'lexical_candidate' });
    else packet.noMatchingPassage.push(section);
  }
  return packet;
}
