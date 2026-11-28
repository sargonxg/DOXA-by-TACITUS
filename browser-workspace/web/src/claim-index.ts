export type EvidenceWindow = {text: string; page?: number; firstLine: number; lastLine: number};
export type RankedWindow = EvidenceWindow & {relevance: number};
const stop = new Set('about after again also among and are been before being both but can could did does each either from had has have into more most only other our over paper result results said shall should some study than that the their them then there these they this those through under using was were what when where which while will with would'.split(' '));
const wordPattern = /[\p{L}\p{N}_]+/gu;
export const words = (text: string) => text.toLowerCase().match(wordPattern) ?? [];
const useful = (word: string) => word.length > 1 && !stop.has(word);

type Candidate = {window: EvidenceWindow; counts: Map<string,number>; length: number};
export class ClaimIndex {
  private readonly query: Set<string>;
  private readonly df = new Map<string,number>();
  private readonly candidates: Candidate[] = [];
  private lengths = 0;
  private total = 0;

  constructor(claim: string) {
    this.query = new Set(words(claim).filter(useful));
  }

  add(window: EvidenceWindow) {
    const tokens = words(window.text);
    if (tokens.length < 5) return;
    this.total++;
    this.lengths += tokens.length;
    const counts = new Map<string,number>();
    for (const token of tokens) {
      if (this.query.has(token)) counts.set(token,(counts.get(token) ?? 0)+1);
    }
    if (!counts.size) return;
    for (const token of counts.keys()) this.df.set(token,(this.df.get(token) ?? 0)+1);
    this.candidates.push({window,counts,length:tokens.length});
  }

  get size() { return this.total; }

  shortlist(limit = 8) {
    const mean = this.lengths / Math.max(1,this.total);
    const list: RankedWindow[] = [];
    for (const {window,counts,length} of this.candidates) {
      let relevance = 0;
      for (const [term,tf] of counts) {
        const df = this.df.get(term)!;
        const idf = Math.log1p((this.total - df + .5)/(df + .5));
        relevance += idf * tf * 2.2 / (tf + 1.2*(.25 + .75*length/mean));
      }
      const local = list.findIndex(item => item.relevance < relevance);
      if (local<0 && list.length>=limit) continue;
      if (list.some(item => item.text===window.text)) continue;
      if (local<0) list.push({...window,relevance});
      else list.splice(local,0,{...window,relevance});
      if (list.length>limit) list.pop();
    }
    return list;
  }
}

function chunks(text: string, characters: number) {
  const out: string[] = [];
  let rest = text;
  while (rest.length>characters) {
    let cut = rest.lastIndexOf(' ',characters);
    if (cut<characters/2) cut=characters;
    if (/[\uDC00-\uDFFF]/.test(rest[cut])) cut--;
    out.push(rest.slice(0,cut));
    rest=rest.slice(cut).trimStart();
  }
  if (rest) out.push(rest);
  return out;
}

export function indexLines(index: ClaimIndex, lines: string[], first = 1, page?: number) {
  const limit = 950;
  let firstLine = first;
  let pending = '';
  function flush(lastLine: number) {
    if (pending) index.add({text:pending,page,firstLine,lastLine});
    pending='';
  }
  lines.forEach((line,i)=>{
    const position = first+i;
    const safe = line.trim();
    if (!safe) { flush(position-1); return; }
    if (safe.length>limit) {
      flush(position-1);
      for (const text of chunks(safe,limit)) index.add({text,page,firstLine:position,lastLine:position});
      return;
    }
    if (pending.length+safe.length+1>limit) flush(position-1);
    if (!pending) firstLine=position;
    pending+=(pending ? '\n' : '')+safe;
  });
  flush(first+lines.length-1);
}

export function abstractOnPage(lines:string[], page?:number) {
  if (page!==undefined && page>6) return undefined;
  const heading=/^(?:#{1,6}\s+)?abstract(?:\s*[:.—]\s*(.*)|\s*)$/i;
  const ending=/^(?:#{1,6}\s+)?(?:\d+[.)]\s*)?(?:introduction|keywords?|author summary|funding|competing interests|copyright|citation|background and introduction)\b/i;
  const first=lines.findIndex(text=>heading.test(text.trim()));
  if (first<0) return undefined;
  const inline=lines[first].trim().match(heading)?.[1] ?? '';
  const buffer=[inline].filter(Boolean);
  let end=first;
  for (let i=first+1;i<lines.length;i++) {
    const text=lines[i].trim();
    if (ending.test(text) || buffer.join('\n').length+text.length>2350) break;
    if (!text && buffer.join('\n').length>80) break;
    if (text) {buffer.push(text);end=i;}
  }
  const text=buffer.join('\n');
  return text.length>=100 ? {text,page,firstLine: inline ? first+1 : first+2,lastLine:end+1} : undefined;
}
