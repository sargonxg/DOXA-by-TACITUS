import { abstractOnPage, ClaimIndex, indexLines } from './claim-index';
import type { EvidenceWindow } from './claim-index';
import type { ScoredWindow } from './verifier-math';
import { best,corroborates,labels,output,softmax,summarize } from './verifier-math';
import type { MODEL } from './verifier-math';
import type { ClaimCommand, ClaimEvent, ClaimResult, Lead } from './claim-contract';
import { startVerifier } from './claim-runtime';

const emit=(event: ClaimEvent)=>self.postMessage(event);
let session: Awaited<ReturnType<typeof startVerifier>> | undefined;
let adapter: MODEL | undefined;
let selector: MODEL | undefined;
let job: {index:ClaimIndex; claim:string; base:string; pdfPages?:number; unreadablePages:number; emptyPages:number; abstract?:EvidenceWindow} | undefined;

async function load(name: string,base: string) {
  const response=await fetch(new URL(name,base));
  if(!response.ok) throw new Error('The local verifier configuration is unavailable.');
  return await response.json() as MODEL;
}

async function finish(current: NonNullable<typeof job>) {
  const {index,claim,base,pdfPages,unreadablePages,emptyPages}=current;
  const shortlisted=index.shortlist();
  const result: ClaimResult={
    method:'doxa-claim-selector-v1',claim,verdict:'insufficient_context',scores:null,leads:[],
    passages:index.size,shortlisted:shortlisted.length,screened:0,pdfPages,unreadablePages,emptyPages,
  };
  if (shortlisted.length) {
    if (!session || !adapter || !selector) {
      emit({kind:'status',text:'Loading the pinned verifier · first run downloads about 185 MB…'});
      [session,adapter,selector]=await Promise.all([
        startVerifier(base),load('adapter.json',base),load('selector.json',base),
      ]);
    }
    const scored: ScoredWindow[]=[];
    for (let i=0;i<shortlisted.length;i++) {
      emit({kind:'status',text:`Checking passage ${i+1} of ${shortlisted.length} locally…`});
      const window=shortlisted[i];
      const logits=await session(window.text,claim);
      if (logits) scored.push({...window,logits});
    }
    result.screened=scored.length;
    if (scored.length) {
      const summary=summarize(scored,claim,adapter);
      const scores=output(selector,summary.features);
      result.scores=scores;
      const top=best(scores);
      result.verdict=labels[top];
      const makeLead=(i: number):Lead=>{
        const row=summary.leads[i].row;
        const {logits:_,...window}=row.window;
        return {window,verdict:labels[i],rawScores:row.raw};
      };
      // Strong competing readings from distinct passages require review.
      const competing=summary.leads[0].row.window.text!==summary.leads[1].row.window.text
        && summary.leads[0].row.raw[0]>=.80 && summary.leads[1].row.raw[1]>=.80
        && summary.leads[0].row.relative>=.40 && summary.leads[1].row.relative>=.40;
      result.leads=[makeLead(top)];
      if (competing) result.leads.push(...[0,1].filter(i=>i!==top).map(makeLead));
      if (current.abstract && top!==2) {
        const abstract=current.abstract;
        emit({kind:'status',text:'Checking the abstract separately…'});
        const logits=await session(abstract.text,claim);
        result.abstractChecked=Boolean(logits);
        if (logits && corroborates(adapter,abstract.text,claim,logits,top)
            && !result.leads.some(lead=>lead.window.text===abstract.text)) {
          result.leads.push({window:{...abstract,relevance:0},verdict:labels[top],rawScores:softmax(logits),abstract:true});
        }
      }
    }
  }
  job=undefined;
  emit({kind:'result',result});
}

self.addEventListener('message',async (event: MessageEvent<ClaimCommand>)=>{
  const command=event.data;
  try {
    if (command.kind==='check-excerpts') {
      const index=new ClaimIndex(command.claim);
      for (const passage of command.passages) index.add(passage);
      await finish({index,claim:command.claim,base:command.base,emptyPages:0,unreadablePages:0});
    } else if (command.kind==='begin') {
      job={index:new ClaimIndex(command.claim),claim:command.claim,base:command.base,
        pdfPages:command.pdfPages,unreadablePages:0,emptyPages:0};
      emit({kind:'ack'});
    } else if (command.kind==='page' && job) {
      if (!command.lines) job.unreadablePages++;
      else if (!command.lines.some(line=>line.trim())) job.emptyPages++;
      else {
        indexLines(job.index,command.lines,1,command.page);
        job.abstract ??=abstractOnPage(command.lines,command.page);
      }
      emit({kind:'ack'});
    } else if (command.kind==='text' && job) {
      const bytes=await command.file.arrayBuffer();
      let data:string;
      try {data=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}
      catch {throw new Error('invalid_utf8');}
      const lines=data.split(/\r\n|\r|\n/);
      indexLines(job.index,lines);
      job.abstract=abstractOnPage(lines.slice(0,180));
      await finish(job);
    } else if (command.kind==='finish' && job) await finish(job);
  } catch (error) {
    job=undefined;
    const message=error instanceof Error && error.message==='invalid_utf8'
      ? 'Choose a readable UTF-8 text document.'
      : 'The local verifier could not finish. Check the model download and browser memory, then retry.';
    emit({kind:'error',text:message});
  }
});
