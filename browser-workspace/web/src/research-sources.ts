type License = { identifier: string; url: string | null };
export type ResearchOrigin = {
  documentId: string; passageId: string; title: string; sourceUrl: string;
  sourceXPath: string; paragraphIndex: number; textSha256: string;
  license: License; topicCandidates: string[];
};
export type ResearchExample = {
  id: string; document_id: string; text: string; text_sha256: string;
  paragraph_index: number; source_section: string; source_xpath: string;
  source_url: string; license: License;
};
export type ResearchSource = {
  id: string; title: string; doi: string | null; url: string; year: number | null;
  authors: string[]; author_count: number; topic_candidates: string[];
  language: string | null;
  matched_title_terms: string[]; corpus_slice: string; text_status: string; example_ids: string[];
};
type Dimension = {
  id: string; name: string; definition: string; required_evidence: string[];
  acquisition_topics: string[]; current_capability: { status: string; scope: string };
  source_refs: string[];
  comparison_requirement: { required: boolean; details: string };
};
type Index = {
  counts: { documents: number; licensed_passages: number; browser_examples: number };
  taxonomy: { dimensions: Dimension[]; references: Record<string, { title: string; url: string }> }; sources: ResearchSource[]; examples: ResearchExample[];
};

const dataset = 'https://huggingface.co/datasets/Giuliocc/doxa-research-atlas';

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  return item;
}

export function mountResearchSources(onAudit: (source: ResearchSource, example: ResearchExample) => void) {
  const panel = document.getElementById('research-panel') as HTMLDetailsElement;
  const status = document.getElementById('research-status')!;
  const dimensions = document.getElementById('research-dimension') as HTMLSelectElement;
  const search = document.getElementById('research-search') as HTMLInputElement;
  const textOnly = document.getElementById('research-text-only') as HTMLInputElement;
  const more = document.getElementById('research-more') as HTMLButtonElement;
  let index: Index | undefined;
  let loading = false;
  let visible = 15;

  function render() {
    if (!index) return;
    const selected = index.taxonomy.dimensions.find(item => item.id === dimensions.value);
    const definition = document.getElementById('research-definition')!;
    definition.replaceChildren();
    if (selected) {
      definition.append(node('p', selected.definition),
        node('p', 'Current capability · ' + selected.current_capability.status.replaceAll('_', ' ') + '. ' + selected.current_capability.scope));
      const evidence = node('details');
      evidence.append(node('summary', 'Evidence this question needs'));
      const list = node('ul');
      for (const requirement of selected.required_evidence) list.append(node('li', requirement));
      evidence.append(list, node('p', selected.comparison_requirement.details));
      for (const id of selected.source_refs) {
        const reference = index.taxonomy.references[id];
        if (!reference) continue;
        const line = node('p');
        const link = node('a', reference.title);
        link.href = reference.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        line.append(link);
        evidence.append(line);
      }
      definition.append(evidence);
    }
    const terms = search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    const matching = index.sources.filter(source => {
      const text = (source.title + ' ' + source.authors.join(' ')).toLocaleLowerCase();
      return terms.every(term => text.includes(term)) && (!textOnly.checked || source.example_ids.length > 0)
        && (!selected || selected.acquisition_topics.some(topic => source.topic_candidates.includes(topic)));
    });
    document.getElementById('research-count')!.textContent = `${matching.length.toLocaleString()} sources match · showing ${Math.min(visible, matching.length)}. Topics record retrieval-query membership. Relevance and bias require contextual review.`;
    const output = document.getElementById('research-results')!;
    output.replaceChildren();
    for (const source of matching.slice(0, visible)) {
      const article = node('article');
      const heading = node('h4');
      const title = node('a', source.title);
      title.href = source.url;
      title.target = '_blank';
      title.rel = 'noopener noreferrer';
      heading.append(title);
      article.append(heading, node('p', [source.year, source.authors.slice(0, 3).join(', ') + (source.author_count > 3 ? ' et al.' : '')].filter(Boolean).join(' · ')),
        node('p', source.corpus_slice.replaceAll('_', ' ') + ' · ' + source.text_status.replaceAll('_', ' ')),
        node('p', 'Matched title terms · ' + (source.matched_title_terms.join(', ') || 'See retrieval record')));
      const record = node('a', 'Research metadata on Hugging Face');
      record.href = dataset + '/viewer/documents/corpus';
      record.target = '_blank';
      record.rel = 'noopener noreferrer';
      article.append(record);
      for (const id of source.example_ids) {
        const example = index.examples.find(item => item.id === id);
        if (!example) continue;
        const details = node('details');
        details.append(node('summary', 'Licensed example · ' + example.source_section + ' paragraph ' + example.paragraph_index),
          node('blockquote', example.text), node('p', example.license.identifier + ' · ' + example.source_xpath));
        const action = node('button', 'Audit this excerpt');
        action.type = 'button';
        action.className = 'outline';
        action.onclick = () => onAudit(source, example);
        details.append(node('p', 'The excerpt may discuss a bias, quote a claim or describe a safeguard. Its topic supplies no positive bias label.'), action);
        if (source.language && !/^en(?:g|-|$)/i.test(source.language)) {
          action.hidden = true;
          details.append(node('p', 'This source is recorded in ' + source.language + '. The deployed screen supports English.'));
        }
        article.append(details);
      }
      if (!source.example_ids.length) article.append(node('p', 'This browser record provides bibliographic metadata. Open the original source for its full context.'));
      output.append(article);
    }
    more.hidden = visible >= matching.length;
  }

  panel.addEventListener('toggle', async () => {
    if (!panel.open || index || loading) return;
    loading = true;
    status.textContent = 'Loading the public research catalogue…';
    try {
      const response = await fetch(dataset + '/resolve/v0.1.0/research-index.json');
      if (!response.ok) throw new Error('Research index unavailable');
      index = await response.json();
      if (!index || !Array.isArray(index.sources) || !Array.isArray(index.examples) || !Array.isArray(index.taxonomy?.dimensions)) throw new Error('Invalid research index');
      dimensions.replaceChildren(new Option('All research questions', ''), ...index.taxonomy.dimensions.map(item => new Option(item.name, item.id)));
      status.textContent = `${index.counts.documents.toLocaleString()} distinct research candidates · ${index.counts.licensed_passages.toLocaleString()} licensed source paragraphs · ${index.taxonomy.dimensions.length} operational questions. Relevance and bias annotations await contextual review.`;
      document.getElementById('research-controls')!.hidden = false;
      render();
    } catch {
      index = undefined;
      const link = node('a', 'Open the research dataset');
      link.href = dataset;
      status.replaceChildren('The catalogue could not load. ', link);
    } finally { loading = false; }
  });
  dimensions.onchange = search.oninput = textOnly.onchange = () => { visible = 15; render(); };
  more.onclick = () => { visible += 15; render(); };
}
