import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {chromium} from 'playwright';

const web=resolve(fileURLToPath(new URL('..',import.meta.url)));
const out=resolve(web,'../improvement_release/pdf');
const compact=process.argv.includes('--compact');
const languageFile=resolve(web,compact?'node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz':'public/ocr/lang/eng.traineddata.gz');
const languageBytes=await readFile(languageFile);
const languageSha256=createHash('sha256').update(languageBytes).digest('hex');
await mkdir(resolve(out,'fixtures'),{recursive:true});
const server=spawn(process.execPath,['node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','8158','--strictPort'],{cwd:web,stdio:'ignore'});
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined)});
try {
  for(let attempt=0;attempt<30;attempt++) {
    try {await fetch('http://127.0.0.1:8158/');break;}
    catch {await new Promise(resolve=>setTimeout(resolve,100));}
  }
  const context=await browser.newContext();
  if (compact) await context.route('**/ocr/lang/eng.traineddata.gz',route=>route.fulfill({
    path:resolve(web,'node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz'),contentType:'application/gzip',
  }));
  const requests=[];
  context.on('request',request=>requests.push({url:request.url(),method:request.method(),body:request.postData()}));
  const page=await context.newPage();
  page.on('console',message=>{if(message.type()==='error')console.log('Browser diagnostic',message.text());});
  await page.goto('http://127.0.0.1:8158/');
  const result=await page.evaluate(async()=>{
    const {extractPdf}=await import('/src/pdf-text.ts');
    const encoder=new TextEncoder();
    function concat(parts) {
      const arrays=parts.map(part=>typeof part==='string'?encoder.encode(part):part);
      const output=new Uint8Array(arrays.reduce((sum,part)=>sum+part.length,0));
      let offset=0;
      for(const array of arrays){output.set(array,offset);offset+=array.length;}
      return output;
    }
    function pdf(nodes) {
      const pieces=['%PDF-1.4\n'];
      const offsets=[0];
      let offset=encoder.encode(pieces[0]).length;
      for(const [i,node] of nodes.entries()) {
        offsets.push(offset);
        const piece=concat([`${i+1} 0 obj\n`,node,'\nendobj\n']);
        pieces.push(piece);offset+=piece.length;
      }
      const xref=offset;
      pieces.push(`xref\n0 ${offsets.length}\n0000000000 65535 f \n`+offsets.slice(1).map(value=>String(value).padStart(10,'0')+' 00000 n \n').join('')
        +`trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
      return concat(pieces);
    }
    function stream(text) {return concat([`<< /Length ${encoder.encode(text).length} >>\nstream\n`,text,'\nendstream']);}
    const canvas=document.createElement('canvas');
    canvas.width=1200;canvas.height=1550;
    const paint=canvas.getContext('2d');
    paint.fillStyle='white';paint.fillRect(0,0,1200,1550);paint.fillStyle='black';
    paint.font='bold 48px Arial';paint.fillText('DOXA SCANNED FIXTURE',85,160);
    paint.font='34px Arial';
    const phrases=['This observational study reported an association.',
      'It cannot establish that the policy caused turnout.',
      'The fictional sample contains 148 residents.',
      'Read the original scan before making a judgment.'];
    phrases.forEach((text,i)=>paint.fillText(text,85,260+i*80));
    const jpeg=Uint8Array.from(atob(canvas.toDataURL('image/jpeg',.98).split(',')[1]),char=>char.charCodeAt(0));
    canvas.width=canvas.height=0;
    const scanned=pdf([
      '<< /Type /Catalog /Pages 2 0 R >>',
      '<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>',
      concat([`<< /Type /XObject /Subtype /Image /Width 1200 /Height 1550 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`,jpeg,'\nendstream']),
      stream('q 612 0 0 792 0 0 cm /Im0 Do Q'),
    ]);
    const columnText=[];
    for(let row=0;row<5;row++) {
      columnText.push(`BT /F1 10 Tf 1 0 0 1 40 ${690-row*18} Tm (Left column row ${row} describes the sample.) Tj ET`);
      columnText.push(`BT /F1 10 Tf 1 0 0 1 330 ${690-row*18} Tm (Right column row ${row} describes the results.) Tj ET`);
    }
    const columns=pdf([
      '<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
      '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
      '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',stream(columnText.join('\n')),
    ]);
    async function review(bytes,name,options={}) {
      const pages=[],quality=[];
      await extractPdf(new File([bytes],name,{type:'application/pdf'}),{active:()=>true,start:async()=>{},
        page:async(page,lines)=>{pages.push({page,lines});},quality:record=>quality.push(record),cancelWith:()=>{}},options);
      return {pages,quality};
    }
    const untreated=await review(scanned,'SCAN_PRIVATE_FIXTURE.pdf');
    const column=await review(columns,'COLUMN_PRIVATE_FIXTURE.pdf');
    const limited=await review(scanned,'SCAN_LIMIT_FIXTURE.pdf',{ocr:true,maxOcrPages:0});
    const ocrStarted=performance.now();
    const recognized=await review(scanned,'SCAN_PRIVATE_FIXTURE.pdf',{ocr:true});
    const ocrMilliseconds=performance.now()-ocrStarted;
    const papers=[];
    for(const path of ['political-bias-study.pdf','political-influencers.pdf']) {
      const response=await fetch('/examples/'+path);
      const original=await response.arrayBuffer();
      const parsed=await review(original,path);
      papers.push({name:path,pages:parsed.pages.length,characters:parsed.quality.reduce((n,q)=>n+q.characters,0),
        twoColumnPages:parsed.quality.filter(q=>q.columns===2).map(q=>q.page),
        excludedMargins:parsed.quality.reduce((n,q)=>n+q.excludedMargins.length,0),
        tableWarningPages:parsed.quality.filter(q=>q.tableRows).map(q=>q.page),
        smallTextPages:parsed.quality.filter(q=>q.smallTextLines).map(q=>q.page),errors:parsed.quality.filter(q=>q.source==='error').length,
        firstPageLines:parsed.pages[0].lines.slice(0,9)});
    }
    let binary='';
    for(let i=0;i<scanned.length;i+=32768)binary+=String.fromCharCode(...scanned.subarray(i,i+32768));
    return {untreated,column,limited,recognized,ocrMilliseconds,papers,scannedBase64:btoa(binary)};
  });
  await writeFile(resolve(out,compact?'observations-compact.json':'observations.json'),JSON.stringify({...result,scannedBase64:undefined},null,2)+'\n');
  assert.equal(result.untreated.quality[0].source,'image_only');
  assert.equal(result.untreated.quality[0].nativeCharacters,0);
  assert.equal(result.untreated.quality[0].characters,0);
  assert(result.untreated.quality[0].issues.includes('ocr_available'));
  assert.equal(result.column.quality[0].columns,2);
  assert(result.column.pages[0].lines.slice(0,5).every(line=>line.startsWith('Left column')));
  assert(result.column.pages[0].lines.slice(5).every(line=>line.startsWith('Right column')));
  assert(result.limited.quality[0].issues.includes('ocr_page_limit'));
  assert.equal(result.recognized.quality[0].source,'ocr');
  assert(result.recognized.quality[0].characters>100);
  assert.match(result.recognized.pages[0].lines.join(' '),/observational study/i);
  assert.match(result.recognized.pages[0].lines.join(' '),/cannot establish/i);
  assert(result.recognized.quality[0].lines.every(line=>line.box[0]>=0 && line.box[1]>=0));
  assert.deepEqual(result.papers.map(paper=>paper.pages),[23,18]);
  assert(result.papers.every(paper=>paper.characters>20000 && paper.errors===0));
  assert(requests.every(request=>request.method==='GET' && request.body===null));
  assert(!JSON.stringify(requests).includes('PRIVATE_FIXTURE'));
  assert(!requests.some(request=>!request.url.startsWith('http://127.0.0.1:8158/')));
  await writeFile(resolve(out,'fixtures/scanned.pdf'),Buffer.from(result.scannedBase64,'base64'));
  const {scannedBase64,untreated,column,limited,recognized,...rest}=result;
  const summary={method:'doxa-pdf-quality-acceptance-v1',passed:true,languageDataFile:languageFile,
    languageDataSha256:languageSha256,languageDataBytes:languageBytes.length,...rest,
    scanned:{untreated:untreated.quality[0],recognized:recognized.quality[0],text:recognized.pages[0].lines.join('\n')},
    columns:{quality:column.quality[0],lines:column.pages[0].lines},limited:limited.quality[0],
    network:{requests:requests.length,allGetOnly:true,allSameOrigin:true,sourcePayloadSent:false}};
  await writeFile(resolve(out,compact?'results-compact.json':'results.json'),JSON.stringify(summary,null,2)+'\n');
  console.log(JSON.stringify({passed:true,ocrMilliseconds:result.ocrMilliseconds,ocrText:recognized.pages[0].lines.join(' '),papers:result.papers,network:summary.network},null,2));
} finally {await browser.close();server.kill();}
