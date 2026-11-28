import { MAX_DOCUMENT_BYTES } from './document-audit';
import type { DocumentCommand, DocumentEvent, DocumentReport, Finding, Progress, SourceType } from './document-audit';
import type { ClaimCommand, ClaimEvent, ClaimResult } from './claim-contract';
import { claimLabels } from './research-claims';
import type { ClaimKind } from './research-claims';
import { caseFile, dispositions, download, preservationChecks } from './review-case';
import type { Decision } from './review-case';
import { criterionHelp, explanations } from './criterion-guide';
import { corpusSummary, countSummary, distribution, percent, poolCounts } from './report-analysis';
import { mountResearchSources } from './research-sources';
import type { ResearchOrigin } from './research-sources';
import { separateEvidence, studyPacket } from './study-evidence';
import { mountProjectControls } from './review-projects';
import type { ProjectSnapshot, JsonValue } from './project-format';
import { buildComparisonCohorts, comparisonFields } from './comparison-cohorts';
import { comparisonDocuments, renderComparisonEditor } from './comparison-ui';
import { renderSourceQuality } from './source-quality-view';
import type { PdfPageQuality } from './pdf-layout';
import { trackReviewActivity } from './review-activity';
import { createFindingReading, renderMitigationReview } from './finding-reading';
import { checkMitigation, explainFinding } from './finding-explanation';
import { createWordingWorker } from './context-wording';
import type { WordingEvent, WordingResult } from './context-wording';

const byId = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const number = new Intl.NumberFormat('en-US');
const count = (n: number) => number.format(n);
const labels: Record<string,string> = {
  causal_language:'Causal wording', citation_marker:'Citation marker', study_design:'Study design',
  sample_size:'Sample-size wording', qualification:'Qualification', disclosed_limitation:'Reported limitation',
  opening:'Opening or unclassified',
  identity_derogation_exclusion:'Identity derogation or exclusion',
  evidential_overclaim:'Evidence overclaim',
  sampling_risk:'Sampling or selection risk',
  measurement_risk:'Measurement or coding risk',
  confounding:'Confounding or endogeneity',
  selective_reporting:'Selective reporting or publication bias',
  attrition:'Attrition or missing data',
  generalizability:'Generalizability or representativeness',
};
const label = (s: string) => labels[s] ?? s.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());
const checks = [
  ['evaluative_framing','Framing'], ['collective_blame','Blame'],
  ['identity_stereotyping','Stereotype'], ['identity_derogation_exclusion','Exclusion'],
  ['group_conspiracy','Conspiracy'], ['evidential_overclaim','Overclaim'],
] as const;
const reviewCues=new Set(['study_design','disclosed_limitation','sampling_risk','measurement_risk','confounding','selective_reporting','attrition','generalizability']);
type SourceDocument = {file:File; author:string; doi?:string; researchSource?:ResearchOrigin;metadata?:Record<string,string>;sha256?:string};
type ReviewedDocument = SourceDocument & {report:DocumentReport; sample:boolean};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, value?: string) {
  const node=document.createElement(tag);
  if (value!==undefined) node.textContent=value;
  return node;
}

function table(headers: (string | Node)[], rows: (string | Node)[][]) {
  const wrapper=element('div');
  wrapper.className='table-scroll';
  const grid=element('table');
  const head=grid.createTHead().insertRow();
  for (const title of headers) {
    const cell=element('th');
    cell.append(title);
    cell.scope='col';
    head.append(cell);
  }
  const body=grid.createTBody();
  for (const values of rows) {
    const row=body.insertRow();
    const title=element('th');
    title.append(values[0]);
    title.scope='row';
    row.append(title);
    for (const value of values.slice(1)) row.insertCell().append(value);
  }
  wrapper.append(grid);
  return wrapper;
}

function location(finding: Pick<Finding,'page'|'firstLine'|'lastLine'>) {
  const lines=finding.firstLine===finding.lastLine ? 'line '+finding.firstLine : 'lines '+finding.firstLine+'–'+finding.lastLine;
  return finding.page ? 'PDF page '+count(finding.page)+' · extracted '+lines : lines;
}

function findingTitle(finding:Finding) {
  return finding.category==='claim' ? claimLabels[finding.check as ClaimKind] : label(finding.check);
}

function findingKey(finding:Finding, index:number) {
  return [index,finding.category,finding.check,finding.passage,finding.focus?.start].join(':');
}

export function mountDocumentDesk() {
  const fileInput=byId<HTMLInputElement>('document-file');
  const run=byId<HTMLButtonElement>('document-run');
  const cancel=byId<HTMLButtonElement>('document-cancel');
  const example=byId<HTMLButtonElement>('document-example');
  const meter=byId<HTMLProgressElement>('document-progress');
  const filter=byId<HTMLSelectElement>('document-filter');
  const status=byId('document-status');
  let worker: Worker | undefined;
  let stopJob: (()=>void) | undefined;
  let checkpoint: DocumentReport | undefined;
  let activeReport: DocumentReport | undefined;
  let fileName='';
  let sample=false;
  let sourceUrl:string | undefined;
  let verifier:Worker | undefined;
  let reviewing=false;
  let batch=false;
  let canceled=false;
  let selectedDocument=0;
  let authorView=false;
  let sectionCheck='evaluative_framing';
  let findingSection:string|undefined;
  let documents:ReviewedDocument[]=[];
  let failures:string[]=[];
  let decisionsChanged=false;
  let projectBusy=false;
  let comparisonScope='all';
  let auditMs=0;
  let projectExtra:Record<string,JsonValue>={};
  let snapshotExtra:Record<string,unknown>={};
  let wordingWorker:Worker|undefined;
  let wordingBusy=false;
  const checked = new Map<string,{document:number; result:ClaimResult}>();
  const wordingChecks = new Map<string,{document:number; result:WordingResult}>();
  const decisions = new Map<string,Decision>();
  const caseDownload=byId<HTMLButtonElement>('review-export');
  const activity=trackReviewActivity();

  function running(value: boolean) {
    fileInput.disabled=example.disabled=value;
    for (const input of byId('source-author-fields').querySelectorAll('input')) input.disabled=value;
    run.disabled=value || !fileInput.files?.length;
    cancel.hidden=meter.hidden=!value;
    byId<HTMLInputElement>('document-ocr').disabled=value;
    projectControls.setRunning(value);
  }

  function authorFields() {
    const fields=byId('source-author-fields');
    fields.replaceChildren();
    const files=Array.from(fileInput.files ?? []).slice(0,12);
    byId('source-authors').hidden=files.length===0;
    for (const [i,file] of files.entries()) {
      const row=element('div');
      const caption=element('label',file.name);
      const input=element('input');
      input.id='source-author-'+i;
      input.type='text';
      input.maxLength=120;
      input.placeholder='Author or organization';
      caption.htmlFor=input.id;
      row.append(caption,input);
      fields.append(row);
    }
  }

  function renderCorpus() {
    byId('corpus-overview').hidden=documents.length<2;
    if (documents.length<2) return;
    const cohorts=buildComparisonCohorts(comparisonDocuments(documents));
    const scope=byId<HTMLSelectElement>('comparison-scope');
    scope.replaceChildren(new Option('All documents · descriptive view','all'),...cohorts.matchedGroups.map(group=>new Option(
      group.documents.length+' matched documents · '+comparisonFields.map(field=>group.metadata[field]).join(' · '),group.key)));
    if (!cohorts.matchedGroups.some(group=>group.key===comparisonScope)) comparisonScope='all';
    scope.value=comparisonScope;
    const activeGroup=cohorts.matchedGroups.find(group=>group.key===comparisonScope);
    const members=activeGroup?.documentIds ?? documents.map((_,index)=>String(index));
    byId('comparison-scope-note').textContent=activeGroup
      ? 'The matrix uses the declared matching context for '+members.length+' documents. Check source selection and evidence comparability.'
      : `${cohorts.matchedDocumentCount} of ${cohorts.documentCount} documents have a declared matching peer. The matrix describes all supplied files.`;
    const picker=byId<HTMLSelectElement>('corpus-document');
    picker.replaceChildren(...documents.flatMap((item,i)=>members.includes(String(i))?[new Option(item.file.name,String(i))]:[]));
    picker.value=String(selectedDocument);
    byId('compare-authors').parentElement!.hidden=!documents.some(doc=>doc.author);
    const groups=new Map<string,ReviewedDocument[]>();
    const rows=documents.map((doc,i)=>({doc,i})).filter(({i})=>members.includes(String(i)));
    for (const {doc,i} of rows) {
      const key=authorView ? doc.author : String(i);
      const group=groups.get(key) ?? [];
      group.push(doc);
      groups.set(key,group);
    }
    const matrixRows:(string|Node)[][]=[];
    for (const [key,group] of groups) {
      const title=authorView
        ? (key || 'No author label') + ' · '+count(group.length)+(group.length===1 ? ' document' : ' documents')
        : group[0].file.name+(group[0].report.complete ? '' : ' (partial)');
      const record:(string|Node)[]=[title, ...checks.map(([check])=>{
        const counts=poolCounts(group.map(({report})=>report.criteria[check]));
        const {total,resolved}=countSummary(counts);
        const flags=counts.present;
        const cell=element('div');
        const ratio=total ? `${count(flags)}/${count(total)}` : '—';
        const value=!authorView && flags
          ? checkButton(ratio,'model:'+check,`Inspect ${label(check)} in ${group[0].file.name}`)
          : element('span',ratio);
        if (value instanceof HTMLButtonElement) value.onclick=()=>{
          selectDocument(Number(key));
          chooseCheck('model:'+check);
        };
        cell.append(value,distribution(counts),element('small',`${count(resolved)}/${count(total)} resolved`));
        if (total && resolved*2<total) cell.className='low-coverage';
        return cell;
      })];
      matrixRows.push(record);
    }
    byId('corpus-matrix').replaceChildren(table([authorView?'Author':'Document', ...checks.map(([key,short])=>criterionHelp(key,short))], matrixRows));
    byId('compare-documents').setAttribute('aria-pressed',String(!authorView));
    byId('compare-authors').setAttribute('aria-pressed',String(authorView));
  }

  function renderRunAnalysis() {
    const target=byId('run-analysis');
    const {coverage,unresolved}=corpusSummary(documents,checks);
    target.replaceChildren(element('h3','Read this run'));
    target.firstElementChild!.id='run-analysis-title';
    target.append(element('p',`${count(documents.length)} ${documents.length===1?'document':'documents'} · ${count(coverage.screened)} of ${count(coverage.eligible)} eligible passages screened`
      +(coverage.pages ? ` · ${count(coverage.pages)} PDF pages visited` : '')+'.'));
    if(auditMs) target.append(element('p',`Processing took ${(auditMs/1000).toFixed(1)} seconds in this browser, including extraction and model loading. Reviewer effort is recorded separately in exported activity data.`));
    if (failures.length || canceled || documents.some(({report})=>!report.complete)) {
      target.append(element('p','This run has incomplete coverage. Review the per-document status before comparing the returned counts.'));
    }
    if (coverage.gaps) target.append(element('p',`${count(coverage.gaps)} pages have extraction gaps. Image-only pages need OCR before their wording can be reviewed.`));
    if (unresolved?.unresolved) {
      const line=element('p');
      line.append(`${unresolved.title} has the largest unresolved workload, ${count(unresolved.unresolved)} of ${count(unresolved.total)} checks. `,
        `It raised ${count(unresolved.counts.present)} flags (${percent(unresolved.counts.present,unresolved.total)} of assessed passages). `,
        'A review decision needs the passage, its attribution and its supporting evidence.');
      target.append(line);
    }
    target.append(element('p','The same passage can trigger several criteria. Counts describe this selected corpus and its model outputs. Topic, genre and source selection affect comparisons.'));
  }

  function renderWorkedGuide() {
    const target=byId('worked-guide');
    target.hidden=!documents.some(doc=>doc.sample);
    if (target.hidden) return;
    const heading=element('h3','Investigate the worked corpus');
    heading.id='worked-guide-title';
    target.replaceChildren(heading,element('p','Two CC BY research papers examine different questions. Use this example to compare language, inspect uncertainty and test a flag against its source. The reading prompts below were authored for this demonstration.'));
    const tasks=element('div');
    tasks.className='worked-tasks';
    const entries=[
      {doi:'10.1371/journal.pone.0321418',title:'Check the measurement',
        body:'The news-outlet study estimates bias from textual features and reference labels. Ask what the labels measure, how their coverage affects the model, and which political settings the result supports.',
        check:'cue:measurement_risk',fallback:'cue:generalizability',action:'Read measurement and scope'},
      {doi:'10.1371/journal.pone.0321592',title:'Follow the claim to its limit',
        body:'The influencer study combines content analysis and a post-election survey. Compare self-reported helpfulness with a causal claim about votes, and inspect the authors’ stated limits on linking the data sources.',
        check:'cue:disclosed_limitation',fallback:'claim:negation',action:'Read the causal limitation',pattern:/cannot identify causal effects/i},
      {doi:'10.1371/journal.pone.0321418',title:'Challenge a model flag',
        body:'Figure captions, quotations and descriptions of earlier work can trigger a false alarm. Read the flagged Figure 1 caption on page 6, then decide whether the text supports an overclaim concern.',
        check:'model:evidential_overclaim',fallback:'model:evidential_overclaim',action:'Inspect the caption',page:6},
    ];
    for (const task of entries) {
      const index=documents.findIndex(doc=>doc.doi===task.doi);
      if (index<0) continue;
      const source=documents[index];
      const available=new Set(source.report.retainedFindings.map(item=>item.category+':'+item.check));
      const check=[task.check,task.fallback].find(key=>available.has(key));
      const article=element('article');
      article.append(element('h4',task.title),element('p',task.body));
      if (!check || (task.page && !source.report.retainedFindings.some(item=>item.category+':'+item.check===check && item.page===task.page))) {
        const link=element('a','Read the original paper');
        link.href='https://doi.org/'+task.doi;
        article.append(element('p','The corresponding excerpt was unavailable in this run.'),link);
        tasks.append(article);
        continue;
      }
      const button=checkButton(task.action,check);
      button.onclick=()=>{
        selectDocument(index);
        chooseCheck(check);
        if (task.page || task.pattern) {
          const finding=source.report.retainedFindings.find(item=>item.category+':'+item.check===check &&
            (!task.page || item.page===task.page) && (!task.pattern || task.pattern.test(item.text)));
          const detail=finding && document.getElementById('finding-'+findingKey(finding,index)) as HTMLDetailsElement|null;
          if (detail) {detail.open=true;detail.querySelector('summary')?.focus();}
        }
      };
      article.append(button);
      tasks.append(article);
    }
    target.append(tasks);
  }

  function renderSectionAnalysis(report:DocumentReport) {
    const target=byId('section-analysis');
    const parts=Object.entries(report.sectionCriteria ?? {});
    target.hidden=parts.length<2;
    if (target.hidden) return;
    const heading=element('h3','Where the flags occur');
    heading.id='section-analysis-title';
    const caption=element('label','Criterion by detected section');
    const picker=element('select');
    picker.id='section-check';
    caption.htmlFor=picker.id;
    picker.append(...checks.map(([key])=>new Option(label(key),key)));
    picker.value=sectionCheck;
    picker.onchange=()=>{sectionCheck=picker.value;renderSectionAnalysis(report);};
    const rows=parts.map(([section,criteria])=>{
      const counts=poolCounts([criteria[sectionCheck]]);
      const {total,resolved}=countSummary(counts);
      const value=counts.present
        ? checkButton(`${counts.present}/${total}`,'model:'+sectionCheck,`Inspect ${label(sectionCheck)} in ${label(section)}`)
        : `0/${total}`;
      if (value instanceof HTMLButtonElement) value.onclick=()=>chooseCheck('model:'+sectionCheck,section);
      return [label(section),value,distribution(counts),`${resolved}/${total}`];
    });
    const ranked=parts.map(([section,criteria])=>({section,counts:poolCounts([criteria[sectionCheck]])}))
      .sort((a,b)=>b.counts.present-a.counts.present);
    const leader=ranked[0];
    const total=report.criteria[sectionCheck]?.present ?? 0;
    const observation=total ? `${label(leader.section)} contains ${leader.counts.present} of ${total} ${label(sectionCheck).toLowerCase()} flags. `
      +'Section size and resolution coverage affect that count. Inspect the wording and attribution before drawing a conclusion.'
      : `No ${label(sectionCheck).toLowerCase()} flags were raised in the screened sections. Unresolved checks remain visible in the bars.`;
    target.replaceChildren(heading,caption,picker,
      element('p',observation),
      table(['Section','Flagged / assessed','Decisions','Resolved'],rows),
      element('p','Section headings are detected from extracted text. Counts include every eligible passage in the detected section. Open a flag and check its source placement.'));
    target.lastElementChild!.className='muted';
  }

  function selectDocument(index:number) {
    const doc=documents[index];
    if (!doc) return;
    selectedDocument=index;
    findingSection=undefined;
    fileName=doc.file.name;
    sample=doc.sample;
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl=doc.report.source==='pdf' ? URL.createObjectURL(doc.file) : undefined;
    renderCorpus();
    render(doc.report);
    renderComparisonEditor(documents,selectedDocument,(indices,metadata)=>{
      for (const index of indices) documents[index].metadata={...documents[index].metadata,...metadata};
      decisionsChanged=true;
      projectControls.changed();
      selectDocument(selectedDocument);
    });
  }

  function chooseCheck(value:string, section?:string) {
    filter.value=value;
    findingSection=section;
    renderFindings();
    filter.focus();
  }

  function checkButton(text:string, check:string, accessibleName?:string) {
    const button=element('button',text);
    button.type='button';
    button.className='link-button';
    if (accessibleName) button.setAttribute('aria-label',accessibleName);
    button.onclick=()=>chooseCheck(check);
    return button;
  }

  function sourceLink(page:number | undefined) {
    if (!page || !sourceUrl) return undefined;
    const link=element('a','Open PDF page '+count(page));
    link.href=sourceUrl+'#page='+page;
    link.target='_blank';
    link.rel='noopener noreferrer';
    return link;
  }

  function renderCheck(target:HTMLElement, result:ClaimResult) {
    const labels = {
      supported:'Retrieved passages support the wording',
      contradicted:'Retrieved passages conflict with the wording',
      insufficient_context:'The retrieved passages leave the wording unresolved',
    };
    target.replaceChildren(element('p',labels[result.verdict]),
      element('p',`${count(result.screened)} passages checked in Methods and Results.`));
    for (const lead of result.leads) {
      const {window}=lead;
      target.append(element('p',location(window)),element('blockquote',window.text));
      const link=sourceLink(window.page);
      if (link) target.append(link);
    }
    if (result.studyPacket) {
      const packet=element('details');
      packet.append(element('summary','Study design, results and limitations'),
        element('p','These passages were retrieved by shared terms for contextual reading. Reconcile them with the claim and the verifier’s selected evidence.'));
      for (const {section,window} of result.studyPacket.sections) {
        packet.append(element('h4',label(section)+' · '+location(window)),element('blockquote',window.text));
        const link=sourceLink(window.page);
        if (link) packet.append(link);
      }
      if (result.studyPacket.unavailable.length) packet.append(element('p','No separate indexed passage available in '+result.studyPacket.unavailable.map(label).join(', ')+'.'));
      if (result.studyPacket.noMatchingPassage.length) packet.append(element('p','No shared-term passage found in '+result.studyPacket.noMatchingPassage.map(label).join(', ')+'.'));
      target.append(packet);
    }
  }

  function addContextCheck(parent:HTMLElement,item:Finding) {
    if (!item.focus || item.focus.partial || !['direct_with_marker','direct_without_marker'].includes(item.check)
        || !activeReport?.supportPassages.length) return;
    const focus=item.focus;
    const claim=item.text.slice(focus.start,focus.end);
    const documentIndex=selectedDocument;
    const key=findingKey(item,documentIndex);
    const box=element('div');
    box.id='check-result-'+key;
    const status=element('p');
    status.setAttribute('role','status');
    const button=element('button','Check against Methods and Results');
    button.type='button';
    button.className='outline';
    const controls=element('div');
    controls.id='check-controls-'+key;
    const prior=checked.get(key);
    if (prior) renderCheck(box,prior.result);
    else {
      const hint=element('p','First use downloads about 185 MB for the local evidence model.');
      hint.className='muted';
      controls.append(button,hint);
      parent.append(controls);
    }
    parent.append(status,box);
    button.onclick=()=>{
      if (reviewing) { status.textContent='Another evidence check is running.'; return; }
      if (!activeReport) return;
      const packet=studyPacket(claim,item.passage,activeReport);
      const passages=separateEvidence(claim,item.passage,activeReport.supportPassages);
      if (!passages.length) {status.textContent='No separate Methods or Results passage was available.'; return;}
      try {
        verifier ??=new Worker(new URL('./claim-worker.ts',import.meta.url),{type:'module'});
      } catch {
        status.textContent='The local evidence engine could not start.';
        return;
      }
      reviewing=true;
      button.disabled=true;
      status.textContent='Searching the study sections…';
      const selected=verifier;
      const fail=(message:string)=>{
        selected.terminate();
        verifier=undefined;
        reviewing=false;
        button.disabled=false;
        status.textContent=message;
      };
      selected.onmessage=({data}:MessageEvent<ClaimEvent>)=>{
        if (verifier!==selected) return;
        if (data.kind==='status') status.textContent=data.text;
        if (data.kind==='error') fail(data.text);
        if (data.kind==='result') {
          reviewing=false;
          data.result.studyPacket=packet;
          checked.set(key,{document:documentIndex,result:data.result});
          projectControls.changed();
          status.textContent='';
          if (documentIndex===selectedDocument) {
            document.getElementById(controls.id)?.remove();
            const current=document.getElementById(box.id);
            if (current) renderCheck(current,data.result);
          }
        }
      };
      selected.onerror=()=>fail('The local evidence engine stopped.');
      const command:ClaimCommand={kind:'check-excerpts',claim,passages,base:new URL('./verifier/',document.baseURI).href};
      selected.postMessage(command);
    };
  }

  function renderDecisions() {
    byId('review-handoff').hidden=decisions.size===0;
    const list=byId('review-list');
    list.replaceChildren();
    for (const [key,decision] of decisions) {
      const {document:index,finding,disposition}=decision;
      const row=element('li');
      const button=checkButton(`${dispositions[disposition]}${decision.mitigation ? ' + revision draft' : ''} · ${findingTitle(finding)} · ${documents[index].file.name} · ${location(finding)}`,'');
      button.onclick=()=>{
        selectDocument(index);
        chooseCheck(finding.category+':'+finding.check);
        const target=document.getElementById('finding-'+key) as HTMLDetailsElement|null;
        if (target) {
          target.open=true;
          target.querySelector('summary')?.focus();
        }
      };
      row.append(button);
      list.append(row);
    }
  }

  function renderWording(target:HTMLElement,result:WordingResult) {
    target.replaceChildren(element('h4','Contextual wording check'),
      element('p',result.decision==='unresolved' ? 'Wording decision unresolved · '+label(result.reason ?? 'unknown') :
        result.decision==='wording_flag' ? 'Wording flagged for contextual review' : 'No wording flag on this check'),
      element('p',result.scope),element('p',result.score_calibration));
    if (result.model_score!==null) target.append(element('p','Biased-wording class score · '+result.model_score.toFixed(3)));
    target.append(element('p',`${result.token_count}/${result.maximum_tokens} tokens · ${result.model_id}@${result.model_revision.slice(0,12)}`),
      element('p','This sentence-level wording result needs its speaker, quotation and document context.'));
  }

  function addWordingCheck(parent:HTMLElement,item:Finding,key:string) {
    const panel=element('details');
    panel.className='wording-check';
    panel.append(element('summary','Check wording with the contextual model'));
    const button=element('button','Run wording check');
    button.type='button';
    button.className='outline';
    const hint=element('p','Optional local model. Trained on expert-annotated English news sentences. First use downloads the pinned model. Academic text requires separate validation.');
    const status=element('p');
    status.setAttribute('role','status');
    const result=element('div');
    result.id='wording-result-'+key;
    if (wordingChecks.has(key)) {renderWording(result,wordingChecks.get(key)!.result);button.textContent='Run wording check again';}
    panel.append(hint,button,status,result);
    parent.append(panel);
    const index=selectedDocument;
    button.onclick=()=>{
      if (wordingBusy) {status.textContent='Another wording check is running.';return;}
      try {wordingWorker ??=createWordingWorker();}
      catch {status.textContent='The local wording engine could not start.';return;}
      const current=wordingWorker;
      const id=crypto.randomUUID();
      wordingBusy=button.disabled=true;
      current.onmessage=({data}:MessageEvent<WordingEvent>)=>{
        if (wordingWorker!==current || data.id!==id) return;
        if (data.kind==='status') status.textContent=data.text;
        else {
          wordingBusy=button.disabled=false;
          if(data.kind==='error') status.textContent=data.text;
          else {
            wordingChecks.set(key,{document:index,result:data.result});
            projectControls.changed();
            status.textContent='Wording check finished locally.';
            const active=document.getElementById(result.id);
            if(active) renderWording(active,data.result);
          }
        }
      };
      current.onerror=()=>{
        if(wordingWorker!==current)return;
        current.terminate();wordingWorker=undefined;wordingBusy=button.disabled=false;
        status.textContent='The local wording engine stopped. Retry this check.';
      };
      current.postMessage({kind:'check',id,text:item.focus ? item.text.slice(item.focus.start,item.focus.end) : item.text,
        base:new URL('./wording/',document.baseURI).href});
    };
  }

  function addDecision(parent:HTMLElement, finding:Finding, key:string) {
    const current=decisions.get(key);
    const reading=createFindingReading(finding,current?.annotations);
    const index=selectedDocument;
    const editor=element('details');
    editor.className='review-editor';
    const summary=element('summary',current ? dispositions[current.disposition] : 'Record a review decision');
    const form=element('div');
    const select=element('select');
    select.id='decision-'+key;
    select.append(new Option('Select an outcome',''),...Object.entries(dispositions).map(([value,title])=>new Option(title,value)));
    select.value=current?.disposition ?? '';
    const caption=element('label','Reviewer decision');
    caption.htmlFor=select.id;
    const note=element('textarea');
    note.id='note-'+key;
    note.maxLength=3000;
    note.rows=3;
    note.value=current?.note ?? '';
    const noteCaption=element('label','Reason or next step (optional)');
    noteCaption.htmlFor=note.id;
    const save=element('button','Save decision');
    save.type='button';
    save.disabled=!select.value;
    select.onchange=()=>{save.disabled=!select.value;};
    const clear=element('button','Clear decision');
    clear.type='button';
    clear.className='outline';
    clear.hidden=!current;
    const status=element('p');
    status.className='status';
    status.setAttribute('role','status');
    const mitigation=element('details');
    mitigation.className='mitigation-editor';
    mitigation.append(element('summary','Draft a precise revision'));
    mitigation.append(element('p',explanations[finding.check]?.action ?? 'Align the wording with the actual source, speaker, evidence and scope.'));
    mitigation.append(element('p','Draft an optional revision and check its meaning. Save it with the review decision.'));
    const draft=element('textarea');
    draft.id='draft-'+key;
    draft.rows=4;
    draft.maxLength=6000;
    draft.value=current?.mitigation?.proposedText ?? '';
    const draftCaption=element('label','Proposed wording');
    draftCaption.htmlFor=draft.id;
    const purpose=element('textarea');
    purpose.id='purpose-'+key;
    purpose.rows=2;
    purpose.maxLength=1500;
    purpose.value=current?.mitigation?.purpose ?? '';
    const purposeCaption=element('label','What the revision should improve');
    purposeCaption.htmlFor=purpose.id;
    const copy=element('button','Start from original');
    copy.type='button';
    copy.className='outline';
    copy.onclick=()=>{
      if (draft.value.trim()) {draft.focus();return;}
      draft.value=finding.focus ? finding.text.slice(finding.focus.start,finding.focus.end) : finding.text;
      updateMeaning();
      draft.focus();
    };
    const checklist=element('fieldset');
    checklist.append(element('legend','Meaning checks by the reviewer'));
    const boxes=Object.entries(preservationChecks).map(([check,text])=>{
      const caption=element('label');
      const box=element('input');
      box.type='checkbox';
      box.checked=current?.mitigation?.checks[check as keyof typeof preservationChecks] ?? false;
      caption.append(box,text);
      checklist.append(caption);
      return {key:check as keyof typeof preservationChecks,box};
    });
    const meaning=element('div');
    const original=finding.focus ? finding.text.slice(finding.focus.start,finding.focus.end) : finding.text;
    function updateMeaning() {
      meaning.replaceChildren();
      if (draft.value.trim()) meaning.append(renderMitigationReview(original,draft.value.trim()));
    }
    draft.oninput=updateMeaning;
    updateMeaning();
    mitigation.append(copy,draftCaption,draft,meaning,purposeCaption,purpose,checklist);
    const actions=element('div');
    actions.className='actions';
    actions.append(save,clear);
    form.append(reading.element,caption,select,noteCaption,note,mitigation,actions,status);
    editor.append(summary,form);
    parent.append(editor);
    save.onclick=()=>{
      const disposition=select.value as Decision['disposition'];
      if (!dispositions[disposition]) return;
      const checks={scope:false,position:false,evidence:false};
      for (const {key,box} of boxes) checks[key]=box.checked;
      const mitigation=draft.value.trim() ? {status:'reviewer_draft' as const,proposedText:draft.value.trim(),purpose:purpose.value.trim(),checks} : undefined;
      decisions.set(key,{document:index,finding,disposition,note:note.value.trim(),recordedAt:new Date().toISOString(),mitigation,
        annotations:reading.readAnnotations()});
      decisionsChanged=true;
      projectControls.changed();
      summary.textContent=dispositions[disposition];
      clear.hidden=false;
      const handoff=element('a','Open review decisions');
      handoff.href='#review-handoff';
      status.replaceChildren('Decision saved. ',handoff);
      renderDecisions();
    };
    clear.onclick=()=>{
      decisions.delete(key);
      decisionsChanged=true;
      projectControls.changed();
      summary.textContent='Record a review decision';
      select.value=note.value='';
      draft.value=purpose.value='';
      updateMeaning();
      for (const {box} of boxes) box.checked=false;
      save.disabled=clear.hidden=true;
      status.textContent='Decision cleared.';
      select.focus();
      renderDecisions();
    };
  }

  function progress(p: Progress, source: SourceType) {
    const maximum=source==='pdf' ? p.totalPages : p.totalBytes;
    if (maximum) {
      meter.max=maximum;
      meter.value=source==='pdf' ? p.pages : p.bytes;
    } else meter.removeAttribute('value');
    status.textContent=(source==='pdf' && p.totalPages ? `Read ${count(p.pages)} of ${count(p.totalPages)} PDF pages · ` : '')
      + `${count(p.passages)} passages processed · ${count(p.screened)} screened`;
  }

  function renderFindings() {
    const output=byId('document-findings');
    output.replaceChildren();
    if (!activeReport) return;
    const items=activeReport.retainedFindings.filter(item=>filter.value===item.category+':'+item.check && (!findingSection || item.section===findingSection));
    const [category,check]=filter.value.split(':');
    const sectionCounts=findingSection && activeReport.sectionCriteria[findingSection as keyof typeof activeReport.sectionCriteria]?.[check];
    const explanation=explanations[check];
    const reading=byId('criterion-reading');
    reading.replaceChildren();
    if (explanation) {
      reading.append(criterionHelp(check),element('p',explanation.meaning));
      if (category==='model') {
        const counts=sectionCounts || activeReport.criteria[check];
        const {total,resolved}=countSummary(counts);
        reading.append(element('p',`${findingSection ? label(findingSection) : 'Whole document'} · ${counts.present} of ${total} assessed passages flagged (${percent(counts.present,total)}). `
          +`${resolved} of ${total} checks resolved. Among resolved checks, ${counts.present} of ${resolved} flagged (${percent(counts.present,resolved)}).`));
      } else reading.append(element('p',explanation.boundary));
      reading.append(element('p',explanation.question));
    }
    const seen=sectionCounts ? sectionCounts.present : activeReport.sampling.seenByCheck[filter.value];
    const sectionNote=byId('section-filter-note');
    sectionNote.hidden=!findingSection;
    sectionNote.replaceChildren();
    if (findingSection) {
      const clear=checkButton('Show all sections',filter.value);
      sectionNote.append('Showing '+label(findingSection)+'. ',clear);
    }
    byId('document-sample-note').textContent=seen ? `${count(items.length)} of ${count(seen)} matched excerpts available${items.length<seen ? ' · sampled from the full count' : ''}.` : '';
    if (!items.length) {
      let message='No excerpts are retained for this selection.';
      if (seen) message='Flags were counted in this section, but no excerpt from this section was retained in the bounded sample. Show all sections to inspect the available evidence.';
      else if (!activeReport.progress.screened) message='No passages were screened. Check the extraction and eligibility counts.';
      else if (!filter.value) message='No model flags in the screened passages. Select a research cue if one is available.';
      output.append(element('p',message));
      return;
    }
    for (const item of items) {
      const details=element('details');
      details.className='document-finding';
      const key=findingKey(item,selectedDocument);
      details.addEventListener('toggle',()=>{if(details.open) activity.open(key);});
      details.id='finding-'+key;
      const title=findingTitle(item);
      details.append(element('summary',title+' · '+location(item)));
      const classification={claim:'Sentence match',cue:'Text cue',model:'Model flag for review'}[item.category];
      details.append(element('p',classification+' · '+label(item.section)));
      const quote=element('blockquote');
      const start=item.matched ? item.text.indexOf(item.matched) : -1;
      if (item.focus) {
        const {start,end,matchStart,matchEnd,citation}=item.focus;
        const marks=[{start:matchStart,end:matchEnd,citation:false}];
        if (citation && (citation.end<=matchStart || citation.start>=matchEnd)) marks.push({...citation,citation:true});
        let offset=start;
        for (const match of marks.sort((a,b)=>a.start-b.start)) {
          const highlight=element('mark',item.text.slice(match.start,match.end));
          if (match.citation) highlight.className='counter';
          quote.append(item.text.slice(offset,match.start),highlight);
          offset=match.end;
        }
        quote.append(item.text.slice(offset,end));
      } else if (start>=0 && item.matched) {
        quote.append(item.text.slice(0,start),element('mark',item.matched),item.text.slice(start+item.matched.length));
      } else quote.textContent=item.text;
      details.append(quote);
      if (item.focus?.partial) details.append(element('p','This fragment reaches a passage boundary. Open the full passage and PDF to recover the complete claim.'));
      if (item.focus && item.text!==quote.textContent) {
        const full=element('details');
        full.append(element('summary','Full passage'),element('blockquote',item.text));
        details.append(full);
      }
      if (item.context) {
        const surrounding=element('details');
        surrounding.append(element('summary',item.category==='model' ? 'Context used by the model' : 'Adjacent text'),element('blockquote',item.context));
        details.append(surrounding);
      }
      const link=sourceLink(item.page);
      if (link) details.append(link);
      addContextCheck(details,item);
      addWordingCheck(details,item,key);
      addDecision(details,item,key);
      output.append(details);
    }
  }

  function render(report: DocumentReport) {
    activeReport=report;
    const p=report.progress;
    const sections=Object.entries(report.sections);
    const reference=sections.reduce((n,[,item])=>n+item.reference,0);
    const short=sections.reduce((n,[,item])=>n+item.short,0);
    const eligible=sections.reduce((n,[,item])=>n+item.eligible,0);
    byId('document-result-title').textContent=documents.length>1 ? 'Corpus audit' : fileName;
    const coverage=element('p',`${count(p.passages)} passages processed · ${count(p.screened)} of ${count(eligible)} eligible passages screened.`);
    const scope=element('p',(report.complete ? 'Complete scan' : 'Partial scan')
      + (p.totalPages ? ` · ${count(p.pages)} of ${count(p.totalPages)} PDF pages visited.` : '.')
      + (reference ? ` ${count(reference)} reference ${reference===1 ? 'passage' : 'passages'} excluded from model screening.` : '')
      + (short ? ` ${count(short)} short ${short===1 ? 'passage' : 'passages'} excluded.` : ''));
    const gaps=p.emptyPages+p.extractionErrors;
    byId('document-coverage').replaceChildren(coverage,scope);
    if (documents.length>1) byId('document-coverage').prepend(element('p',
      fileName+(documents[selectedDocument].author ? ' · '+documents[selectedDocument].author : '')));
    if (failures.length) byId('document-coverage').append(element('p',
      `Failed or partial sources · ${failures.join(', ')}`));
    if (gaps) byId('document-coverage').append(element('p',`Extraction gaps · ${count(p.emptyPages)} without selectable text · ${count(p.extractionErrors)} page errors.`));
    if (report.supportLimitReached) byId('document-coverage').append(element('p','The Methods and Results evidence index reached its size limit.'));
    if (sample) {
      const source=documents[selectedDocument];
      const credit=element('a','Published example · '+source.author+', PLOS ONE (CC BY)');
      credit.href='https://doi.org/'+source.doi;
      byId('document-coverage').append(credit);
    }
    const origin=documents[selectedDocument]?.researchSource;
    if (origin) {
      const source=element('a',origin.title);
      source.href=origin.sourceUrl;
      source.target='_blank';
      source.rel='noopener noreferrer';
      byId('document-coverage').append(source,
        element('p',`Research excerpt · paragraph ${origin.paragraphIndex} · ${origin.license.identifier}. This audit covers the selected excerpt. Read the original article for full context.`));
    }
    const claims=byId('document-claims');
    claims.replaceChildren();
    const foundClaims=(Object.keys(claimLabels) as ClaimKind[]).filter(key=>report.claimReview.counts[key]);
    if (foundClaims.length) {
      claims.append(element('h3','Causal wording to inspect'));
      const actions=element('ul');
      for (const key of foundClaims) {
        const row=element('li');
        const link=checkButton(`${claimLabels[key]} · ${count(report.claimReview.counts[key]!)}`,'claim:'+key);
        row.append(link);
        actions.append(row);
      }
      claims.append(actions);
    }
    const academic=byId('document-academic');
    academic.replaceChildren();
    byId('academic-details').hidden=Object.keys(report.cues).length===0 && !foundClaims.length;
    if (Object.keys(report.cues).length || foundClaims.length) {
      const cues=Object.entries(report.cues).filter(([key])=>reviewCues.has(key));
      academic.append(element('h3','Research cues'));
      if (cues.length) academic.append(table(['Cue mentioned in text','Passages'],cues.map(([key,n])=>[
        criterionHelp(key,label(key)),checkButton(count(n),'cue:'+key,'Inspect '+label(key)),
      ])));
      else academic.append(element('p','No research cues matched the extracted text.'));
      const outline=element('details');
      outline.append(element('summary','Section coverage'),table(['Section','Screened','Flagged','Eligible','References','Too short'],
        sections.map(([key,item])=>[label(key),count(item.screened),count(item.flagged),count(item.eligible),count(item.reference),count(item.short)])));
      academic.append(outline);
    }
    byId('document-rates').replaceChildren(
      element('p','These are model flags for review. Check the passage and its context before recording a decision.'),
      table(['Criterion','Flags / assessed','Decisions','Resolved'],
        Object.entries(report.criteria).filter(([key])=>!['material_omission','comparative_double_standard'].includes(key)).map(([key,c])=>{
          const {total,resolved}=countSummary(c);
          return [criterionHelp(key,label(key)),c.present ? checkButton(`${count(c.present)}/${count(total)}`,'model:'+key,'Inspect '+label(key)) : `0/${count(total)}`,
            distribution(c),`${count(resolved)}/${count(total)}`];
        })),
    );
    renderSectionAnalysis(report);
    renderSourceQuality(report,page=>sourceLink(page));
    filter.replaceChildren(new Option('Choose a criterion or cue',''));
    const keys=new Set<string>();
    for (const [key] of checks) if (report.criteria[key]?.present) keys.add('model:'+key);
    for (const item of report.retainedFindings) {
      if (item.category==='claim' || (item.category==='cue' && reviewCues.has(item.check))) keys.add(item.category+':'+item.check);
    }
    for (const key of keys) {
      const [group,name]=key.split(':');
      filter.add(new Option(group==='claim' ? claimLabels[name as ClaimKind] : label(name),key));
    }
    filter.value=[...keys].find(key=>key.startsWith('model:')) ?? '';
    renderFindings();
    byId('document-results').hidden=false;
  }

  async function auditFile(file:File, prefix:string):Promise<DocumentReport|undefined> {
    const started=performance.now();
    const quality:PdfPageQuality[]=[];
    const source:SourceType=file.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'text';
    checkpoint=undefined;
    meter.removeAttribute('value');
    status.textContent=prefix+'Starting local review…';
    return new Promise(resolve=>{
      let job:Worker;
      try { job=new Worker(new URL('./document-worker.ts',import.meta.url),{type:'module'}); }
      catch { resolve(undefined); return; }
      worker=job;
      let ack:(()=>void) | undefined;
      let disposePdf:(()=>void) | undefined;
      function stop(report?:DocumentReport) {
        if (report) {
          report.processingMs=Math.round(performance.now()-started);
          if (source==='pdf') report.sourceQuality=quality.filter(page=>page.page<=report.progress.pages).map(page=>{
            const findings=report.retainedFindings.filter(finding=>finding.page===page.page);
            return {...page,originalLineCount:page.lines.reduce((maximum,line)=>Math.max(maximum,line.line),0),
              geometryScope:'retained_findings' as const,
              lines:page.lines.filter(line=>findings.some(finding=>line.line>=finding.firstLine && line.line<=finding.lastLine))};
          });
        }
        worker=undefined;
        job.terminate();
        stopJob=undefined;
        disposePdf?.();
        ack?.();
        resolve(report);
      }
      stopJob=()=>stop(checkpoint?.progress.passages ? checkpoint : undefined);
      job.onmessage=({data}:MessageEvent<DocumentEvent>)=>{
        if (worker!==job) return;
        if (data.kind==='checkpoint') checkpoint=data.report;
        else if (data.kind==='ack') {const ready=ack; ack=undefined; ready?.();}
        else if (data.kind==='status') status.textContent=prefix+data.text;
        else if (data.kind==='progress') {
          progress(data.progress,source);
          status.textContent=prefix+status.textContent;
        } else if (data.kind==='report') stop(data.report);
        else if (data.kind==='error') stop(data.report?.progress.passages ? data.report : undefined);
      };
      job.onerror=()=>{if(worker===job) stopJob?.();};
      const modelUrl=new URL('./model/',document.baseURI).href;
      const send=(command:DocumentCommand)=>job.postMessage(command);
      if (source==='text') {
        send({kind:'text',file,mode:'academic',modelUrl});
        return;
      }
      status.textContent=prefix+'Opening PDF locally…';
      const active=()=>worker===job;
      const wait=(command:DocumentCommand)=>new Promise<void>(done=>{
        ack=done;
        send(command);
      });
      void import('./pdf-text').then(async ({extractPdf})=>{
        if (!active()) return;
        await extractPdf(file,{
          active,
          cancelWith:stopPdf=>{disposePdf=stopPdf;},
          start:pages=>wait({kind:'begin-pdf',mode:'academic',modelUrl,pages,bytes:file.size}),
          page:(page,lines)=>wait({kind:'page',page,lines}),
          quality:page=>quality.push(page),
          status:text=>{status.textContent=prefix+text;},
        },{ocr:byId<HTMLInputElement>('document-ocr').checked});
        if (active()) send({kind:'finish-pdf'});
      }).catch(()=>{if (active()) stopJob?.();});
    });
  }

  async function auditCorpus(sources:SourceDocument[], exampleSource=false) {
    if (batch || projectBusy) return;
    if (!sources.length || sources.length>12 || sources.reduce((size,item)=>size+item.file.size,0)>128*1024*1024 ||
        sources.some(({file})=>!file.size || file.size>MAX_DOCUMENT_BYTES || !/\.(pdf|txt|md)$/i.test(file.name))) {
      running(false);
      status.textContent='Choose up to 12 non-empty PDFs, TXT or Markdown files. Limits are 32 MiB per file and 128 MiB per batch.';
      return;
    }
    if (decisionsChanged && projectControls.isDirty() && !window.confirm('Replace this audit and its changes? Save or download the project first if you need to keep this version.')) {
      running(false);
      status.textContent='Current audit kept. Download your case file to save the review decisions.';
      return;
    }
    batch=true;
    const started=performance.now();
    canceled=false;
    verifier?.terminate();
    verifier=undefined;
    reviewing=false;
    checked.clear();
    wordingWorker?.terminate();
    wordingWorker=undefined;
    wordingBusy=false;
    wordingChecks.clear();
    decisions.clear();
    decisionsChanged=false;
    byId('review-status').textContent='';
    renderDecisions();
    documents=[];
    projectExtra={};
    snapshotExtra={};
    comparisonScope='all';
    projectControls.reset();
    failures=[];
    authorView=false;
    activeReport=undefined;
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    sourceUrl=undefined;
    byId('document-results').hidden=true;
    running(true);
    const seenSources=new Set<string>();
    for (const [i,source] of sources.entries()) {
      source.sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await source.file.arrayBuffer())),byte=>byte.toString(16).padStart(2,'0')).join('');
      if(canceled)break;
      if(seenSources.has(source.sha256)) {failures.push(source.file.name+' (duplicate source skipped)');continue;}
      seenSources.add(source.sha256);
      const prefix=sources.length>1 ? `Document ${i+1} of ${sources.length} · ` : '';
      const report=await auditFile(source.file,prefix);
      if (report) documents.push({...source,report,sample:exampleSource});
      if (!report?.complete && !canceled) failures.push(source.file.name);
      if (canceled) break;
    }
    batch=false;
    auditMs=Math.round(performance.now()-started);
    activity.start();
    running(false);
    if (documents.length) {
      renderRunAnalysis();
      renderWorkedGuide();
      selectDocument(0);
      projectControls.changed();
    }
    const gaps=documents.some(({report})=>report.progress.emptyPages || report.progress.extractionErrors);
    if (canceled) status.textContent=documents.length ? 'Audit canceled. Available results are shown below.' : 'Audit canceled.';
    else if (failures.length) status.textContent='Some files could not be audited. Check PDF size, encryption and selectable text.';
    else status.textContent=gaps ? 'Finished with extraction gaps. Check the coverage below.' : 'Review finished locally.';
  }

  cancel.onclick=()=>{
    canceled=true;
    stopJob?.();
  };
  fileInput.onchange=()=>{
    authorFields();
    running(false);
    status.textContent='';
  };
  byId<HTMLFormElement>('document-form').onsubmit=event=>{
    event.preventDefault();
    const sources=Array.from(fileInput.files ?? []).map((file,i)=>({
      file,author:document.querySelector<HTMLInputElement>('#source-author-'+i)?.value.trim() ?? '',
    }));
    void auditCorpus(sources);
  };
  example.onclick=async ()=>{
    if (batch) return;
    fileInput.disabled=example.disabled=run.disabled=true;
    status.textContent='Opening the published examples…';
    try {
      const examples=[
        ['political-bias-study.pdf','Political bias in news outlets.pdf','Rönnback, Emmery & Brighton','10.1371/journal.pone.0321418'],
        ['political-influencers.pdf','From hashtags to ballots.pdf','Schwemmer & Riedl','10.1371/journal.pone.0321592'],
      ];
      const sources=await Promise.all(examples.map(async ([path,title,author,doi])=>{
        const response=await fetch(new URL('./examples/'+path,document.baseURI));
        if (!response.ok) throw new Error('Example unavailable');
        const file=new File([await response.blob()],title,{type:'application/pdf'});
        return {file,author,doi};
      }));
      await auditCorpus(sources,true);
    } catch {
      running(false);
      status.textContent='The sample is unavailable. Choose a local document to continue.';
    }
  };
  byId('compare-documents').onclick=()=>{authorView=false;renderCorpus();};
  byId('compare-authors').onclick=()=>{authorView=true;renderCorpus();};
  byId<HTMLSelectElement>('comparison-scope').onchange=event=>{
    comparisonScope=(event.target as HTMLSelectElement).value;
    const group=buildComparisonCohorts(comparisonDocuments(documents)).matchedGroups.find(item=>item.key===comparisonScope);
    if(group && !group.documentIds.includes(String(selectedDocument))) selectDocument(Number(group.documentIds[0]));
    else renderCorpus();
    projectControls.changed();
  };
  const picker=byId<HTMLSelectElement>('corpus-document');
  picker.onchange=()=>selectDocument(Number(picker.value));
  filter.onchange=()=>{findingSection=undefined;renderFindings();};
  caseDownload.onclick=async ()=>{
    const status=byId('review-status');
    caseDownload.disabled=true;
    status.textContent='Preparing the local case file…';
    try {
      const reviewed=Array.from(decisions,([key,decision])=>({decision,title:findingTitle(decision.finding),
        location:location(decision.finding),check:checked.get(key)?.result,wording:wordingChecks.get(key)?.result}));
      const methods=document.querySelector<HTMLAnchorElement>('a.methods')!.href;
      const content=await caseFile(documents,reviewed,failures,canceled,methods);
      download(content,'text/html;charset=utf-8','doxa-case-file.html');
      if (reviewed.length===decisions.size && reviewed.every(({decision})=>decisions.get(findingKey(decision.finding,decision.document))===decision)) decisionsChanged=false;
      status.textContent='Case file downloaded.';
    } catch {
      status.textContent='The case file could not be created. The JSON audit still contains your decisions.';
    } finally { caseDownload.disabled=false; }
  };
  byId<HTMLButtonElement>('document-export').onclick=()=>{
    if (!activeReport) return;
    const exportOne=({file,author,doi,report,researchSource,metadata}:ReviewedDocument,index:number)=>{
      const {supportPassages:_,limitationPassages:__,...visible}=report;
      return {fileName:file.name,author,doi,researchSource,metadata,...visible,
        evidenceChecks:[...checked.values()].filter(item=>item.document===index).map(item=>item.result),
        wordingChecks:[...wordingChecks.values()].filter(item=>item.document===index).map(item=>item.result),
        reviewActivity:activity.snapshot(),auditMs,
        reviewDecisions:[...decisions.values()].filter(item=>item.document===index)};
    };
    const content=documents.length===1 && !failures.length && !canceled ? exportOne(documents[0],0) :
      {method:'doxa-corpus-audit-v1',documents:documents.map(exportOne),failedFiles:failures,
        comparisons:buildComparisonCohorts(comparisonDocuments(documents)),reviewActivity:activity.snapshot(),auditMs,
        partial:canceled || failures.length>0 || documents.some(({report})=>!report.complete)};
    download(JSON.stringify(content,null,2)+'\n','application/json','doxa-audit.json');
    decisionsChanged=false;
  };
  function getSnapshot():ProjectSnapshot|undefined {
    if(!documents.length)return;
    return {...snapshotExtra,documents,decisions:[...decisions],evidenceChecks:[...checked],selectedDocument,failures,canceled,
      extra:{...projectExtra,comparisonScope,auditMs,reviewActivity:activity.snapshot() as unknown as JsonValue,
        wordingChecks:[...wordingChecks] as unknown as JsonValue,ocr:byId<HTMLInputElement>('document-ocr').checked}};
  }
  function restoreSnapshot(snapshot:ProjectSnapshot) {
    verifier?.terminate();verifier=undefined;reviewing=false;
    wordingWorker?.terminate();wordingWorker=undefined;wordingBusy=false;
    documents=snapshot.documents;
    decisions.clear();for(const [key,value] of snapshot.decisions)decisions.set(key,value);
    checked.clear();for(const [key,value] of snapshot.evidenceChecks)checked.set(key,value);
    wordingChecks.clear();
    const extra=snapshot.extra ?? {};
    projectExtra=extra;
    snapshotExtra=Object.fromEntries(Object.entries(snapshot).filter(([key])=>!['documents','decisions','evidenceChecks','selectedDocument','failures','canceled','extra'].includes(key)));
    // Known model extensions are checked before use. Unknown extension data
    // stays portable without being rendered as a trusted result.
    if(Array.isArray(extra.wordingChecks)) for(const entry of extra.wordingChecks) {
      if(!Array.isArray(entry) || entry.length!==2 || typeof entry[0]!=='string')continue;
      const value=entry[1] as unknown as {document:number;result:WordingResult};
      const result=value?.result;
      if(!Number.isInteger(value?.document) || !documents[value.document] || !result ||
        result.method!=='doxa-wording-minilm-v1' || result.task!=='biased_wording' || result.context_used!==false ||
        !['wording_flag','no_wording_flag','unresolved'].includes(result.decision) ||
        !['scope','model_id','model_revision','score_calibration'].every(key=>typeof result[key as keyof WordingResult]==='string') ||
        !(result.model_score===null || typeof result.model_score==='number' && result.model_score>=0 && result.model_score<=1) ||
        !Number.isInteger(result.token_count) || !Number.isInteger(result.maximum_tokens))continue;
      const finding=documents[value.document].report.retainedFindings.find(item=>findingKey(item,value.document)===entry[0]);
      if(finding)wordingChecks.set(entry[0],value);
    }
    comparisonScope=typeof extra.comparisonScope==='string'?extra.comparisonScope:'all';
    auditMs=typeof extra.auditMs==='number' && extra.auditMs>=0 ? extra.auditMs : 0;
    activity.start(extra.reviewActivity);
    byId<HTMLInputElement>('document-ocr').checked=extra.ocr===true;
    selectedDocument=snapshot.selectedDocument;
    failures=snapshot.failures;canceled=snapshot.canceled;authorView=false;decisionsChanged=false;
    fileInput.value='';authorFields();
    renderRunAnalysis();renderWorkedGuide();selectDocument(selectedDocument);renderDecisions();
    status.textContent='Saved analysis snapshot restored. The source files remain available for a fresh audit.';
  }
  const projectControls=mountProjectControls({snapshot:getSnapshot,restore:restoreSnapshot,
    canReplace:()=>!batch && !projectBusy && (!documents.length || !projectControls.isDirty() ||
      window.confirm('Replace the current audit and pending changes? Save or download a project first to keep this version.')),
    rerun:()=>{void auditCorpus(documents.map(({file,author,doi,researchSource,metadata})=>({file,author,doi,researchSource,metadata})),documents.every(item=>item.sample));},
    busy:value=>{projectBusy=value;byId('document-results').inert=value;running(batch || value);}});
  mountResearchSources((source,example)=>{
    document.querySelector<HTMLDialogElement>('#bias-guide')!.close();
    const file=new File([example.text],source.title.replace(/[\/\\]/g,'-').slice(0,100)+'.txt',{type:'text/plain'});
    void auditCorpus([{file,author:source.authors.slice(0,2).join(', '),doi:source.doi ?? undefined,
      researchSource:{documentId:source.id,passageId:example.id,title:source.title,sourceUrl:source.url,
        sourceXPath:example.source_xpath,paragraphIndex:example.paragraph_index,textSha256:example.text_sha256,
        license:example.license,topicCandidates:source.topic_candidates}}]);
  });
}
