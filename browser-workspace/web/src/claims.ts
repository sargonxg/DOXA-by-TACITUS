import { MAX_DOCUMENT_BYTES } from './document-audit';
import type { ClaimCommand, ClaimEvent, ClaimResult } from './claim-contract';
import type { Verdict } from './verifier-math';

const verdictText: Record<Verdict,string> = {
  supported:'Source supports the claim',
  contradicted:'Source contradicts the claim',
  insufficient_context:'Insufficient evidence in the retrieved text',
};
const node=<T extends HTMLElement = HTMLElement>(id: string)=>document.getElementById(id) as T;

function element<K extends keyof HTMLElementTagNameMap>(tag: K,text: string) {
  const el=document.createElement(tag);
  el.textContent=text;
  return el;
}

export function mountClaimDesk() {
  const form=node<HTMLFormElement>('check-claim-form');
  const statement=node<HTMLTextAreaElement>('check-claim-input');
  const chosen=node<HTMLInputElement>('check-claim-file');
  const run=node<HTMLButtonElement>('check-claim-run');
  const demo=node<HTMLButtonElement>('check-claim-example');
  const cancel=node<HTMLButtonElement>('check-claim-cancel');
  const status=node('check-claim-status');
  const result=node('check-claim-result');
  const exportButton=node<HTMLButtonElement>('check-claim-export');
  let worker:Worker | undefined;
  let stop:(()=>void) | undefined;
  let record:ClaimResult | undefined;
  let documentUrl:string | undefined;
  let sourceName='';

  function toggle(active: boolean) {
    run.disabled=active;
    demo.disabled=active;
    chosen.disabled=active;
    statement.disabled=active;
    cancel.hidden=!active;
  }

  function render(output: ClaimResult, pdf?: File) {
    record=output;
    node('check-claim-verdict').textContent=verdictText[output.verdict];
    node('check-claim-quoted').textContent=output.claim;
    const coverage=node('check-claim-coverage');
    const pages=output.pdfPages ? `${output.pdfPages.toLocaleString()} PDF pages · ` : '';
    coverage.textContent=`${pages}${output.passages.toLocaleString()} passages searched · ${output.screened} of ${output.shortlisted} retrieved passages checked.`;
    const notes:string[]=[];
    if (output.unreadablePages || output.emptyPages) notes.push(
      `Extraction gaps · ${output.emptyPages} without selectable text · ${output.unreadablePages} page errors.`);
    if (output.screened<output.shortlisted) notes.push('Some retrieved passages exceeded the model’s token limit.');
    if (output.abstractChecked) notes.push('The verifier also checked the detected abstract.');
    if (!output.shortlisted) notes.push('No passage shared searchable words with the claim.');
    node('check-claim-gaps').textContent=notes.join(' ');
    const evidence=node('check-claim-evidence');
    evidence.replaceChildren();
    if (pdf && output.leads.length) documentUrl=URL.createObjectURL(pdf);
    for (const [i,lead] of output.leads.entries()) {
      const item=document.createElement('section');
      item.className='claim-evidence';
      const window=lead.window;
      const range=window.firstLine===window.lastLine ? 'Line '+window.firstLine : `Lines ${window.firstLine}–${window.lastLine}`;
      const location=window.page ? `PDF page ${window.page} · Extracted ${range.toLowerCase()}` : range;
      const title=lead.abstract ? 'Matching abstract passage' : i ? 'Competing passage' :
        output.verdict==='insufficient_context' ? 'Passage to inspect' : 'Lead evidence';
      item.append(element('h3',title),element('p',location),element('blockquote',window.text));
      if (documentUrl && window.page) {
        const link=element('a','Open source page');
        link.href=documentUrl+'#page='+window.page;
        link.target='_blank';
        link.rel='noopener noreferrer';
        item.append(link);
      }
      evidence.append(item);
    }
    result.hidden=false;
  }

  function start(file:File,claim:string) {
    if (stop) return;
    if (!file.size || file.size>MAX_DOCUMENT_BYTES || !/\.(pdf|txt|md)$/i.test(file.name)) {
      status.textContent='Choose a non-empty PDF, UTF-8 text or Markdown file up to 32 MiB.';
      return;
    }
    sourceName=file.name;
    if (documentUrl) { URL.revokeObjectURL(documentUrl); documentUrl=undefined; }
    record=undefined;
    result.hidden=true;
    toggle(true);
    status.textContent='Searching the document locally…';
    try {
      worker ??=new Worker(new URL('./claim-worker.ts',import.meta.url),{type:'module'});
    } catch {
      toggle(false);
      status.textContent='Could not start the browser worker. Reload the page and try again.';
      return;
    }
    const job=worker;
    let disposePdf:(()=>void) | undefined;
    let ack:(()=>void) | undefined;
    const send=(message:ClaimCommand)=>job.postMessage(message);
    const waiting=(message:ClaimCommand)=>new Promise<void>(resolve=>{ack=resolve;send(message);});
    let active=true;
    function complete(terminate:boolean) {
      active=false;
      stop=undefined;
      disposePdf?.();
      ack?.();
      if (terminate) {job.terminate();worker=undefined;}
      toggle(false);
    }
    stop=()=>{
      complete(true);
      status.textContent='Claim check canceled.';
    };
    const isPdf=/\.pdf$/i.test(file.name);
    job.onmessage=({data}:MessageEvent<ClaimEvent>)=>{
      if (!active) return;
      if (data.kind==='ack') { const ready=ack; ack=undefined; ready?.(); }
      else if (data.kind==='status') status.textContent=data.text;
      else if (data.kind==='error') { complete(true); status.textContent=data.text; }
      else if (data.kind==='result') {
        complete(false);
        render(data.result,isPdf ? file : undefined);
        status.textContent='Claim check finished in this browser.';
      }
    };
    job.onerror=()=>{
      if (!active) return;
      complete(true);
      status.textContent='The verifier worker stopped. Try again in a browser with enough memory.';
    };
    const base=new URL('./verifier/',document.baseURI).href;
    if (isPdf) {
      void import('./pdf-text').then(async ({extractPdf})=>{
        if (!active) return;
        let pages=0;
        await extractPdf(file,{
          active:()=>active,
          start:total=>{pages=total; return waiting({kind:'begin',claim,base,pdfPages:total});},
          page:(page,lines)=>{
            if (page===1 || page%40===0 || page===pages) status.textContent=`Searching PDF page ${page} of ${pages}…`;
            return waiting({kind:'page',page,lines});
          },
          cancelWith:fn=>{disposePdf=fn;},
        });
        if (active) send({kind:'finish'});
      }).catch(error=>{
        if (!active) return;
        complete(true);
        status.textContent=error instanceof Error && error.message.includes('5,000')
          ? error.message : 'Could not read this PDF. Try an unlocked PDF or a text export.';
      });
    } else {
      void waiting({kind:'begin',claim,base}).then(()=>{if(active) send({kind:'text',file});});
    }
  }

  form.onsubmit=event=>{
    event.preventDefault();
    const claim=statement.value.trim();
    const file=chosen.files?.[0];
    if (!file || !claim) { status.textContent='Choose a source file and enter a claim.'; return; }
    start(file,claim);
  };
  demo.onclick=()=>{
    statement.value='The transit study randomly assigned stations to treatment and control.';
    const text='The team conducted an observational study of two stations before and after a fare change. No stations were randomly assigned, and the study had no control group. Recorded trips rose from 100 to 120.';
    start(new File([text],'Transit methods example.txt'),statement.value);
  };
  cancel.onclick=()=>stop?.();
  exportButton.onclick=()=>{
    if (!record) return;
    const url=URL.createObjectURL(new Blob([JSON.stringify({sourceName,...record},null,2)+'\n'],{type:'application/json'}));
    const link=element('a','');
    link.href=url;
    link.download='doxa-claim-check.json';
    link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
}
