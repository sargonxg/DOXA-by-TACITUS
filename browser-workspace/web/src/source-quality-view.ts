import type { DocumentReport } from './document-audit';

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}

export function renderSourceQuality(report: DocumentReport, sourceLink: (page: number) => HTMLAnchorElement | undefined) {
  const panel = document.getElementById('source-quality')!;
  const output = document.getElementById('source-quality-content')!;
  const pages = report.sourceQuality ?? [];
  panel.hidden = !pages.length;
  output.replaceChildren();
  if (!pages.length) return;
  const count = (source: string) => pages.filter(page => page.source === source).length;
  output.append(node('p', `${pages.length} page records · ${count('text_layer')} with native text · ${count('ocr')} recognized by OCR · ${count('image_only')} image-only · ${count('no_selectable_text')} without readable text · ${count('error')} extraction errors.`),
    node('p', 'Layout warnings locate material to verify in the original PDF. OCR engine scores describe recognition output and need checking against the image.'));
  const controls = node('div');
  const label = node('label', 'Inspect PDF reading page');
  const picker = node('input');
  picker.type = 'number';
  picker.id = 'quality-page';
  picker.min = '1';
  picker.max = String(report.progress.totalPages ?? pages.length);
  picker.value = String(pages.find(page => page.issues.some(issue => issue !== 'repeated_margin_excluded'))?.page ?? pages[0].page);
  label.htmlFor = picker.id;
  const detail = node('div');
  function render() {
    const page = pages.find(item => item.page === Number(picker.value));
    detail.replaceChildren();
    if (!page) { detail.append(node('p', 'No completed reading record for this page.')); return; }
    detail.append(node('p', `Page ${page.page} · ${page.source.replaceAll('_', ' ')} · ${page.nativeCharacters} native characters · ${page.characters} analyzed characters`),
      node('p', `${page.columns} detected columns · ${page.tableRows} possible table rows · ${page.smallTextLines} small-text lines · ${page.excludedMargins.length} repeated-margin lines excluded.`),
      node('p', 'Reading order · ' + page.readingOrder.replaceAll('_', ' ')));
    if (page.ocrConfidence !== undefined) detail.append(node('p', 'OCR engine score · ' + page.ocrConfidence.toFixed(1) + ' / 100'));
    if (page.issues.length) detail.append(node('p', 'Reading checks · ' + page.issues.map(issue => issue.replaceAll('_', ' ')).join(' · ')));
    const link = sourceLink(page.page);
    if (link) detail.append(link);
    if (page.lines.length) {
      const geometry = node('details');
      geometry.append(node('summary', 'Source coordinates for retained finding lines'));
      const list = node('ul');
      for (const line of page.lines.slice(0, 40)) {
        list.append(node('li', `Line ${line.line} · box ${line.box.map(value => value.toFixed(1)).join(', ')} PDF points · ${line.source}`));
      }
      geometry.append(list, node('p', `${page.lines.length} line boxes retained on this page. The audit JSON contains the available coordinates.`));
      detail.append(geometry);
    }
  }
  picker.oninput = render;
  controls.append(label, picker);
  output.append(controls, detail);
  render();
}
