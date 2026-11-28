import { DocumentAudit, MAX_DOCUMENT_BYTES } from './document-audit';
import type { DocumentCommand, DocumentEvent } from './document-audit';
import { loadModel } from './inference';

const emit = (event: DocumentEvent) => self.postMessage(event);

function reporter(audit: DocumentAudit) {
  let lastProgress = 0;
  let lastCheckpoint = 0;
  return (force = false) => {
    const now = performance.now();
    if (force || now-lastProgress >= 180) {
      emit({kind:'progress',progress:{...audit.progress}});
      lastProgress=now;
    }
    if (force || (audit.progress.passages && now-lastCheckpoint>=1300)) {
      emit({kind:'checkpoint',report:audit.snapshot(false)});
      lastCheckpoint=now;
    }
  };
}

async function readText(file: File, audit: DocumentAudit, notify: ReturnType<typeof reporter>) {
  const reader = file.stream().getReader();
  const decoder = new TextDecoder('utf-8',{fatal:true});
  let remainder = '';
  let line = 1;
  let continued = false;
  const fragmentSize = 4096;
  function feed(text: string, end: boolean) {
    const lines = (remainder+text).split('\n');
    remainder = end ? '' : lines.pop() ?? '';
    for (const value of lines) {
      audit.addLine(value.replace(/\r$/,''),line++,undefined,continued);
      continued=false;
    }
    while (remainder.length > fragmentSize) {
      let boundary=fragmentSize;
      if (/[\uDC00-\uDFFF]/.test(remainder[boundary])) boundary--;
      audit.addLine(remainder.slice(0,boundary),line,undefined,continued);
      remainder=remainder.slice(boundary);
      continued=true;
    }
  }
  try {
    while (true) {
      const {done,value}=await reader.read();
      if (done) break;
      feed(decoder.decode(value,{stream:true}),false);
      audit.progress.bytes+=value.byteLength;
      notify();
    }
    feed(decoder.decode(),true);
  } finally {
    reader.releaseLock();
  }
}

let audit: DocumentAudit | undefined;
let notify: ReturnType<typeof reporter> | undefined;
self.addEventListener('message',async (event: MessageEvent<DocumentCommand>) => {
  const command=event.data;
  try {
    if (command.kind==='text') {
      if (command.file.size>MAX_DOCUMENT_BYTES || !command.file.size) {
        throw new Error('Choose a non-empty file no larger than 32 MiB.');
      }
      emit({kind:'status',text:'Loading the local model…'});
      const model=await loadModel(command.modelUrl);
      audit=new DocumentAudit(model,command.mode,'text',command.file.size);
      await readText(command.file,audit,reporter(audit));
      emit({kind:'report',report:audit.snapshot(true)});
    } else if (command.kind==='begin-pdf') {
      const model=await loadModel(command.modelUrl);
      audit=new DocumentAudit(model,command.mode,'pdf',command.bytes);
      audit.progress.totalPages=command.pages;
      notify=reporter(audit);
      notify(true);
      emit({kind:'ack'});
    } else if (command.kind==='page' && audit) {
      if (!command.lines) audit.progress.extractionErrors++;
      else {
        if (!command.lines.some(line=>line.trim())) audit.progress.emptyPages++;
        command.lines.forEach((line,idx)=>audit!.addLine(line,idx+1,command.page));
        audit.finishPage();
      }
      audit.progress.pages++;
      notify?.();
      emit({kind:'ack'});
    } else if (command.kind==='finish-pdf' && audit) {
      audit.progress.bytes=audit.progress.totalBytes;
      emit({kind:'report',report:audit.snapshot(true)});
    }
  } catch {
    emit({kind:'error',text:!audit ? 'The local model could not load. Reload and try again.'
      : command.kind==='text' ? 'Could not read this text file. Use UTF-8 text or Markdown.'
      : 'Document review stopped. Inspect the partial report before continuing.',report:audit?.snapshot(false)});
  }
});
