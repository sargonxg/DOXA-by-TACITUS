/** Conservative PDF reading order with inspectable source geometry. */
export type PdfBox = [number, number, number, number];
export type PdfFragment = {
  text: string; x: number; y: number; width: number; height: number;
  index: number; angle?: number;
};
export type PdfLayoutLine = {
  text: string; box: PdfBox; fragments: number[]; fontSize: number;
};
export type PdfLineLocation = {
  line: number; box: PdfBox; fragments: number[]; source: 'text_layer' | 'ocr';
  confidence?: number;
};
export type PdfExcludedMargin = { line: number; text: string; box: PdfBox; reason: 'repeated_margin' };
export type PdfPageQuality = {
  page: number; width: number; height: number;
  source: 'text_layer' | 'ocr' | 'image_only' | 'no_selectable_text' | 'error';
  nativeCharacters: number; characters: number; columns: number;
  readingOrder: 'geometric_rows' | 'column_heuristic' | 'native_content';
  tableRows: number; smallTextLines: number; imageOperators: number;
  excludedMargins: PdfExcludedMargin[]; issues: string[]; lines: PdfLineLocation[];
  ocrConfidence?: number;
};
export type PdfLayout = {
  lines: PdfLayoutLine[]; columns: number;
  readingOrder: PdfPageQuality['readingOrder']; tableRows: number;
  smallTextLines: number; issues: string[];
};

function median(values: number[]) {
  const sorted = [...values].sort((a,b) => a-b);
  return sorted.length ? sorted[Math.floor(sorted.length/2)] : 10;
}

function makeLine(fragments: PdfFragment[], native = false): PdfLayoutLine {
  const ordered = [...fragments].sort((a,b) => native ? a.index-b.index : a.x-b.x || a.index-b.index);
  let text = '';
  let right = ordered[0].x;
  for (const fragment of ordered) {
    const gap = fragment.x-right;
    if (text && fragment.text && !/\s$/.test(text) && !/^\s/.test(fragment.text)
      && gap > Math.max(1,fragment.height*.13)) text += ' ';
    text += fragment.text;
    right = Math.max(right,fragment.x+fragment.width);
  }
  const left = Math.min(...ordered.map(item => item.x));
  const top = Math.min(...ordered.map(item => item.y));
  const bottom = Math.max(...ordered.map(item => item.y+item.height));
  return { text,box:[left,top,right-left,bottom-top],fragments:ordered.map(item => item.index),
    fontSize:median(ordered.map(item => item.height).filter(value=>value>0)) };
}

function rowChunks(row: PdfFragment[], fontSize: number) {
  const ordered = [...row].sort((a,b) => a.x-b.x || a.index-b.index);
  const chunks: PdfFragment[][] = [[]];
  let right = ordered[0].x;
  for (const fragment of ordered) {
    if (chunks[chunks.length-1].length && fragment.x-right > Math.max(24,fontSize*2.4)) chunks.push([]);
    chunks[chunks.length-1].push(fragment);
    right = Math.max(right,fragment.x+fragment.width);
  }
  return chunks.map(chunk=>makeLine(chunk)).filter(line=>line.text.trim());
}

function columnBand(lines: PdfLayoutLine[], split: number, fontSize: number) {
  const left = lines.filter(line => line.box[0]+line.box[2] <= split+3);
  const right = lines.filter(line => line.box[0] >= split-3);
  if (left.length<3 || right.length<3) return {lines,changed:false};
  if (median(left.map(line=>line.box[2]))<split*.3 || median(right.map(line=>line.box[2]))<split*.3) return {lines,changed:false};
  const overlap = Math.min(Math.max(...left.map(line=>line.box[1])),Math.max(...right.map(line=>line.box[1])))
    - Math.max(Math.min(...left.map(line=>line.box[1])),Math.min(...right.map(line=>line.box[1])));
  if (overlap < fontSize*3 || left.length+right.length!==lines.length) return {lines,changed:false};
  return {lines:[...left,...right],changed:true};
}

export function readingOrder(fragments: PdfFragment[], width: number, height: number): PdfLayout {
  const positioned = fragments.filter(item => item.text.length && Number.isFinite(item.x) && Number.isFinite(item.y));
  if (!positioned.some(item=>item.text.trim())) return {lines:[],columns:0,readingOrder:'geometric_rows',tableRows:0,smallTextLines:0,issues:[]};
  const fontSize = median(positioned.filter(item=>item.text.trim()).map(item=>item.height).filter(value=>value>0));
  // PDF.js can represent a column gutter as one wide synthetic space.
  // Keeping it as a word fragment would join both columns into one line.
  const visible=positioned.filter(item=>item.text.trim() || item.width<=Math.max(12,fontSize));
  const rotated = visible.some(item => Math.abs(item.angle ?? 0)>.08);
  const sorted = [...visible].sort((a,b)=>a.y-b.y || a.x-b.x || a.index-b.index);
  const rows: PdfFragment[][] = [];
  for (const item of sorted) {
    const last = rows[rows.length-1];
    if (!last || Math.abs(item.y-last[0].y)>Math.max(1.8,fontSize*.28)) rows.push([item]);
    else last.push(item);
  }
  const chunks = rows.map(row=>rowChunks(row,fontSize));
  const tableRows = chunks.filter(row=>row.length>=3 && row.filter(line=>line.text.length<=25).length>=2).length;
  const geometric = chunks.flat();
  const smallTextLines = geometric.filter(line=>line.fontSize<fontSize*.78 && line.box[1]>height*.6).length;
  const issues: string[] = [];
  if (tableRows) issues.push('table_layout');
  if (smallTextLines) issues.push('small_text_or_footnotes');
  const complexRows=chunks.filter(row=>row.length>=3 && row.every(line=>line.text.length>25));
  if (rotated || complexRows.length>=3) {
    if (rotated) issues.push('rotated_text');
    if (complexRows.length>=3) issues.push('complex_column_layout');
    // Retain content-stream order when a two-column or axis-aligned
    // decision would be unreliable. Source inspection remains necessary.
    const native: PdfFragment[][] = [];
    for (const item of [...visible].sort((a,b)=>a.index-b.index)) {
      const last=native[native.length-1];
      if (!last || Math.abs(item.y-last[0].y)>Math.max(1.8,fontSize*.28)) native.push([item]);
      else last.push(item);
    }
    return {lines:native.map(items=>makeLine(items,true)).filter(line=>line.text.trim()),
      columns:complexRows.length>=3?Math.max(...complexRows.map(row=>row.length)):1,
      readingOrder:'native_content',tableRows,smallTextLines,issues};
  }
  const split = width/2;
  const full = geometric.filter(line=>line.box[0]<split-3 && line.box[0]+line.box[2]>split+3);
  const ordered: PdfLayoutLine[] = [];
  let band: PdfLayoutLine[] = [];
  let changed = false;
  const flush = () => {
    const result=columnBand(band,split,fontSize);
    ordered.push(...result.lines);
    changed ||= result.changed;
    band=[];
  };
  for (const line of geometric) {
    if (full.includes(line)) {flush();ordered.push(line);}
    else band.push(line);
  }
  flush();
  if (changed) issues.push('column_order_inferred');
  return {lines:ordered,columns:changed?2:1,readingOrder:changed?'column_heuristic':'geometric_rows',tableRows,smallTextLines,issues};
}

export function marginKey(line: PdfLayoutLine, pageHeight: number) {
  const lineHeight=line.box[3];
  const top = line.box[1]<pageHeight*.055;
  const bottom = line.box[1]+lineHeight>pageHeight*.945;
  if ((!top && !bottom) || line.text.length>150) return undefined;
  const normalized=line.text.toLocaleLowerCase().replace(/\d+/g,'#').replace(/\s+/g,' ').trim();
  if (normalized.length<4 && !/^[\d\s/]+$/.test(line.text)) return undefined;
  return (top?'top:':'bottom:')+normalized;
}

export function repeatedMargins(pages: {height:number;lines:PdfLayoutLine[]}[]) {
  const counts=new Map<string,number>();
  if (pages.length<3) return new Set<string>();
  for (const page of pages) {
    const keys=new Set(page.lines.map(line=>marginKey(line,page.height)).filter((key):key is string=>Boolean(key)));
    for (const key of keys) counts.set(key,(counts.get(key) ?? 0)+1);
  }
  const minimum=Math.max(3,Math.ceil(pages.length*.6));
  return new Set([...counts].filter(([,count])=>count>=minimum).map(([key])=>key));
}

export function omitRepeatedMargins(layout: PdfLayout, pageHeight: number, known: Set<string>) {
  const excluded: PdfExcludedMargin[]=[];
  const lines=layout.lines.map((line,index)=>{
    const key=marginKey(line,pageHeight);
    if (!key || !known.has(key)) return line;
    excluded.push({line:index+1,text:line.text,box:line.box,reason:'repeated_margin'});
    return {...line,text:''};
  });
  return {lines,excluded};
}
