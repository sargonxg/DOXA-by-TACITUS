import type { Processed, RecordInput } from './contracts';

export const reviewPolicyVersion = 'doxa-routing-v0.1';
export const byteLimit = 2_000_000;
export const recordLimit = 200;
export const fields = ['record_id','document_id','thread_id','text','context','source_reference'] as const;

function csvRows(input: string): string[][] {
  const rows: string[][] = [];
  let field = '';
  let row: string[] = [];
  let quoted = false;
  let closed = false;
  for (let i = 0; i < input.length; i += 1) {
    const c = input[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (input[i + 1] === '"') { field += '"'; i += 1; }
      else { quoted = false; closed = true; }
      continue;
    }
    if (c === ',' || c === '\n' || c === '\r') {
      row.push(field);
      field = '';
      closed = false;
      if (c === ',') continue;
      if (c === '\r' && input[i+1] === '\n') i += 1;
      if (row.some(Boolean)) rows.push(row);
      row = [];
    } else if (closed) throw new Error('Unexpected text after a quoted CSV field.');
    else if (c === '"') {
      if (field) throw new Error('Unexpected quotation mark in a CSV field.');
      quoted = true;
    } else field += c;
  }
  if (quoted) throw new Error('Unclosed quoted CSV field.');
  if (field || row.length || closed) {
    row.push(field);
    if (row.some(Boolean)) rows.push(row);
  }
  return rows;
}

export function parseCorpus(filename: string, content: string): RecordInput[] {
  const stripped = content.replace(/^\uFEFF/, '');
  let raw: unknown[];
  if (filename.toLowerCase().endsWith('.jsonl')) {
    raw = stripped.split(/\r?\n/).filter(line => line.trim()).map(line => {
      try { return JSON.parse(line) as unknown; }
      catch { return { skip_reason: 'Invalid JSONL object.' }; }
    });
  } else {
    const [header, ...body] = csvRows(stripped);
    const keys = header?.map(item => item.trim());
    if (!keys?.includes('text') || new Set(keys).size !== keys.length) {
      throw new Error('CSV needs a unique header row with a text field.');
    }
    raw = body.map(row => row.length === keys.length ?
      Object.fromEntries(keys.map((name, i) => [name, row[i]])) :
      { skip_reason: 'CSV field count differs from its header.' });
  }
  if (!raw.length) throw new Error('The file contains no records.');
  if (raw.length > recordLimit) throw new Error('This lab accepts up to 200 records per import.');
  const ids = new Set<string>();
  return raw.map((value, index) => {
    const record = typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string,unknown> : {};
    const string = (name: string): string => typeof record[name] === 'string' ? record[name] : '';
    const originalId = string('record_id');
    const fallbackId = `record-${index + 1}`;
    let id = originalId || fallbackId;
    const duplicateId = ids.has(id);
    while (ids.has(id)) id += '-duplicate';
    ids.add(id);
    const text = string('text');
    const context = string('context');
    const skip = string('skip_reason') ||
      (duplicateId ? 'Repeated record ID.' : '') ||
      (!text.trim() ? 'Missing or blank text.' : '') ||
      ([...text].length > 6000 || [...context].length > 6000 ? 'Passage or context exceeds 6,000 Unicode characters.' : '');
    return {
      record_id: id, document_id: string('document_id') || id,
      thread_id: string('thread_id'), text, context, source_reference: string('source_reference'),
      ...(skip ? { skip_reason: skip } : {}),
    };
  });
}

function dataCell(value: unknown): string {
  const str = value === null || value === undefined ? '' : String(value);
  const protectedValue = /^[\u0000-\u0020\uFEFF]*[=+\-@]|^[\t\r\n]/.test(str) ? "'" + str : str;
  return '"' + protectedValue.replaceAll('"', '""') + '"';
}
export function csvExport(rows: Processed[]): string {
  const headers = [...fields, 'group', 'duplicate_of', 'repeated_quotes', 'skip_reason', 'assessment', 'reviews', 'review_policy_version'];
  const lines = rows.map(row => [...fields.map(key => row.input[key]), row.group, row.duplicate_of,
    JSON.stringify(row.repeated_quotes), row.skip_reason, row.assessment ? JSON.stringify(row.assessment) : '', JSON.stringify(row.reviews), reviewPolicyVersion]);
  return [headers, ...lines].map(line => line.map(dataCell).join(',')).join('\r\n') + '\r\n';
}
export function quoteGroups(text: string): string[] {
  return [...new Set(text.split(/\r?\n/).filter(line => /^\s*>/.test(line))
    .map(line => line.replace(/^\s*>+\s*/, '').trim()).filter(Boolean))];
}
