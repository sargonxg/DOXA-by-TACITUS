export const claimLabels = {
  direct_without_marker:'Causal wording · no same-sentence marker',
  direct_with_marker:'Causal wording · same-sentence marker',
  qualified:'Causal wording · qualification nearby',
  negation:'Causal wording · negation nearby',
  attributed:'Causal wording · reporting or quotation nearby',
  question:'Causal wording · question',
};
export type ClaimKind = keyof typeof claimLabels;
export type ClaimLead = {
  kind: ClaimKind;
  start: number;
  end: number;
  matchStart: number;
  matchEnd: number;
  citation?: {start: number; end: number};
  partial?: boolean;
};

// These patterns locate review leads. They do not resolve syntax or credit a source.
const causal = /\b(?:cause[ds]?(?!\s+of\b)|causing|lead(?:s|ing)? to|led to|result(?:s|ed|ing)? in(?!\s+(?:table|figure|appendix|section)\b)|trigger(?:s|ed|ing)|gave rise to|because of|due to(?!\s+(?:be|start|begin|end|arrive|resume|expire)\b)|attribut(?:e[ds]?|ing)\b[^.!?;]{1,52}\bto)\b/iu;
const marker = /\[\s*(?:\d+(?:\s*[,;–-]\s*\d+)*)\s*\]|\((?:[\p{Lu}][\p{L}'’.-]+(?:\s+(?:et\s+al\.?|&\s+[\p{Lu}][\p{L}'’.-]+|and\s+[\p{Lu}][\p{L}'’.-]+))?,?\s+)(?:19|20)\d{2}[a-z]?(?:\s*[;,][^()]+)?\)|\b[\p{Lu}][\p{L}'’.-]+\s*\((?:19|20)\d{2}[a-z]?\)/u;
const reporting = /\b(?:according to|(?:said|says|claim(?:ed|s)|allege[ds]|state[ds]|argue[ds]|report(?:ed|s)|assert(?:ed|s)|conclude[ds])\b(?=\s+that\b|\s*[:,])|(?:[\p{L}][\p{L}'’-]+(?:\s*\((?:19|20)\d{2}[a-z]?\))?)\s+(?:said|says|claim(?:ed|s)|allege[ds]|state[ds]|argue[ds]|report(?:ed|s)|assert(?:ed|s)|conclude[ds]))\b/iu;
const denial = /\b(?:did not|does not|do not|didn['’]t|doesn['’]t|cannot|can not|could not|can['’]t|couldn['’]t|not shown to|no evidence (?:that|of)|failed to|ruled out|denied that|unlikely to)\b/iu;
const laterDenial = /\b(?:was|were)\s+(?:rejected\b|(?:an?\s+)?(?:inference|conclusion|claim)\b[^,;!?]{1,60}\b(?:rejected|disputed|denied)\b)/iu;
const laterReporting = /[,—]\s+[\p{L}'’ -]{1,55}\b(?:said|claimed|alleged|reported|argued|stated|asserted)\b/iu;
const qualification = /\b(?:may|might|could|possibly|potentially|appears? to|seems? to|hypothes(?:i[sz]e[ds]?|is)|if|unclear whether|uncertain whether)\b/iu;
const quotes = /“[^”]+”|"[^"\n]+"|‘[^’]+’/gu;
const segmenter = new Intl.Segmenter('en',{granularity:'sentence'});

function precedes(text: string, pattern: RegExp, boundary: number) {
  const clauses = text.slice(0,boundary).split(/[;—]|,\s+(?:but|yet|whereas|however)\b/iu);
  return pattern.test(clauses[clauses.length-1].slice(-105));
}

function kindOf(sentence: string, match: RegExpExecArray, hasMarker: boolean): ClaimKind {
  if (/\?\s*[”"’']?\s*$/u.test(sentence)) return 'question';
  const suffix = sentence.slice(match.index+match[0].length);
  if (precedes(sentence,denial,match.index) || laterDenial.test(suffix)) return 'negation';
  if (precedes(sentence,qualification,match.index)) return 'qualified';
  if (precedes(sentence,reporting,match.index) || laterReporting.test(suffix)) return 'attributed';
  for (const quote of sentence.matchAll(quotes)) {
    if (quote.index < match.index && match.index + match[0].length < quote.index + quote[0].length) return 'attributed';
  }
  return hasMarker ? 'direct_with_marker' : 'direct_without_marker';
}

export function claimLeads(text: string): ClaimLead[] {
  // PDF line wraps are layout boundaries. Replacing each code unit keeps
  // sentence and highlight offsets anchored to the unmodified source text.
  const reading = text.replace(/[\r\n]/g,' ');
  if (!causal.test(reading)) return [];
  const leads: ClaimLead[] = [];
  for (const {segment,index} of segmenter.segment(reading)) {
    const match = causal.exec(segment);
    if (!match) continue;
    const citation = marker.exec(segment);
    leads.push({
      kind:kindOf(segment,match,Boolean(citation)), start:index, end:index+segment.trimEnd().length,
      matchStart:index+match.index, matchEnd:index+match.index+match[0].length,
      citation:citation ? {start:index+citation.index,end:index+citation.index+citation[0].length} : undefined,
      partial: !/[.!?]["”’')\]\s]*$/u.test(segment),
    });
  }
  return leads;
}
