import { getDocument, GlobalWorkerOptions, OPS, Util } from 'pdfjs-dist';
import type { PDFPageProxy } from 'pdfjs-dist';
import { MAX_PDF_PAGES } from './document-audit';
import { omitRepeatedMargins, readingOrder, repeatedMargins } from './pdf-layout';
import type { PdfFragment, PdfPageQuality } from './pdf-layout';

type Consumer = {
  active: () => boolean;
  start: (pages: number) => Promise<void>;
  page: (page: number, lines: string[] | null) => Promise<void>;
  cancelWith: (fn: () => void) => void;
  quality?: (quality: PdfPageQuality) => void;
  status?: (text: string) => void;
};
export type PdfOptions = {ocr?:boolean;maxOcrPages?:number};

async function pageLayout(page: PDFPageProxy) {
  const viewport=page.getViewport({scale:1});
  const content=await page.getTextContent();
  const fragments: PdfFragment[]=[];
  for (const [index,item] of content.items.entries()) {
    if (!('str' in item)) continue;
    const matrix=Util.transform(viewport.transform,item.transform);
    const angle=Math.atan2(matrix[1],matrix[0]);
    const height=Math.hypot(matrix[2],matrix[3]);
    const width=item.width*viewport.scale;
    const x=matrix[4],y=matrix[5];
    const horizontal=[Math.cos(angle)*width,Math.sin(angle)*width];
    const vertical=[matrix[2],matrix[3]];
    const corners=[[x,y],[x+horizontal[0],y+horizontal[1]],[x+vertical[0],y+vertical[1]],
      [x+horizontal[0]+vertical[0],y+horizontal[1]+vertical[1]]];
    const left=Math.min(...corners.map(point=>point[0]));
    const top=Math.min(...corners.map(point=>point[1]));
    fragments.push({text:item.str,x:left,y:top,width:Math.max(...corners.map(point=>point[0]))-left,
      height:height || item.height,index,angle});
  }
  return {viewport,layout:readingOrder(fragments,viewport.width,viewport.height)};
}

const imageOperations=new Set([OPS.paintImageXObject,OPS.paintInlineImageXObject,OPS.paintImageMaskXObject,
  OPS.paintImageXObjectRepeat,OPS.paintImageMaskXObjectRepeat,OPS.paintSolidColorImageMask]);

export async function extractPdf(file: File, consumer: Consumer, options: PdfOptions = {}) {
  const bytes=await file.arrayBuffer();
  if (!consumer.active()) return;
  GlobalWorkerOptions.workerSrc=new URL('./pdf/pdf.worker.min.mjs',document.baseURI).href;
  const task=getDocument({
    data:new Uint8Array(bytes), useWasm:false, disableFontFace:true, useSystemFonts:false,
    stopAtErrors:true, isOffscreenCanvasSupported:false,
  });
  let destroyed=false;
  let ocr: Awaited<ReturnType<typeof import('./pdf-ocr')['startPdfOcr']>> | undefined;
  let ocrStarting: Promise<Awaited<ReturnType<typeof import('./pdf-ocr')['startPdfOcr']>>> | undefined;
  let rendered: ReturnType<PDFPageProxy['render']> | undefined;
  let ocrPages=0;
  const ocrLimit=options.maxOcrPages ?? 200;
  async function dispose() {
    if (destroyed) return;
    destroyed=true;
    rendered?.cancel();
    if (ocr) await ocr.terminate().catch(()=>{});
    else if (ocrStarting) void ocrStarting.then(engine=>engine.terminate()).catch(()=>{});
    await task.destroy();
  }
  consumer.cancelWith(()=>{ void dispose().catch(()=>{}); });
  try {
    const pdf=await task.promise;
    if (pdf.numPages>MAX_PDF_PAGES) throw new Error('This PDF exceeds 5,000 pages. Split it into volumes before reviewing.');
    if (!consumer.active()) return;
    await consumer.start(pdf.numPages);
    const preview=[];
    // Learning a repeated margin from several pages prevents a single title
    // or an individual footnote from being discarded as a running header.
    for (let number=1;number<=Math.min(8,pdf.numPages) && consumer.active();number++) {
      const page=await pdf.getPage(number).catch(()=>null);
      if (!page) continue;
      try {
        const {viewport,layout}=await pageLayout(page);
        preview.push({height:viewport.height,lines:layout.lines});
      } catch { /* The scan records any page-level extraction failure below. */ }
      finally {page.cleanup();}
    }
    const margins=repeatedMargins(preview);
    for (let number=1;number<=pdf.numPages && consumer.active();number++) {
      const page=await pdf.getPage(number).catch(()=>null);
      let lines: string[] | null = null;
      const quality: PdfPageQuality={page:number,width:0,height:0,source:'error',nativeCharacters:0,characters:0,
        columns:0,readingOrder:'geometric_rows',tableRows:0,smallTextLines:0,imageOperators:0,excludedMargins:[],issues:[],lines:[]};
      if (page) {
        try {
          const {viewport,layout}=await pageLayout(page);
          quality.width=viewport.width;
          quality.height=viewport.height;
          quality.nativeCharacters=layout.lines.reduce((sum,line)=>sum+line.text.trim().length,0);
          quality.columns=layout.columns;
          quality.readingOrder=layout.readingOrder;
          quality.tableRows=layout.tableRows;
          quality.smallTextLines=layout.smallTextLines;
          quality.issues=[...layout.issues];
          const filtered=omitRepeatedMargins(layout,viewport.height,margins);
          quality.excludedMargins=filtered.excluded;
          if (filtered.excluded.length) quality.issues.push('repeated_margin_excluded');
          let extracted=filtered.lines;
          quality.source=quality.nativeCharacters ? 'text_layer' : 'no_selectable_text';
          const operators=await page.getOperatorList().catch(()=>null);
          if (!operators) quality.issues.push('graphics_inspection_failed');
          quality.imageOperators=operators?.fnArray.filter(operation=>imageOperations.has(operation)).length ?? 0;
          if (!quality.nativeCharacters && quality.imageOperators) quality.source='image_only';
          if (quality.nativeCharacters && quality.nativeCharacters<160 && quality.imageOperators) quality.issues.push('sparse_text_layer');
          const needsOcr=(quality.imageOperators>0 && quality.nativeCharacters<160)
            || (quality.nativeCharacters<32 && Boolean(operators?.fnArray.length));
          if (needsOcr && options.ocr && ocrPages<ocrLimit) {
            ocrPages++;
            consumer.status?.('Reading scanned PDF page '+number+' locally…');
            const scale=Math.min(2,Math.sqrt(3_500_000/(viewport.width*viewport.height)));
            const imageViewport=page.getViewport({scale});
            const canvas=document.createElement('canvas');
            canvas.width=Math.ceil(imageViewport.width);
            canvas.height=Math.ceil(imageViewport.height);
            try {
              rendered=page.render({canvas,viewport:imageViewport});
              await rendered.promise;
              rendered=undefined;
              ocrStarting ??=import('./pdf-ocr').then(module=>module.startPdfOcr(consumer.status));
              ocr ??=await ocrStarting;
              if (!consumer.active()) break;
              const result=await ocr.recognize(canvas,scale);
              if (result.lines.length) {
                extracted=result.lines;
                quality.source='ocr';
                quality.ocrConfidence=result.confidence;
                quality.issues.push('ocr_text_requires_verification');
                if (result.confidence<70) quality.issues.push('low_ocr_score');
              } else quality.issues.push('ocr_returned_no_text');
            } catch {
              if (consumer.active()) quality.issues.push('ocr_failed');
            } finally {rendered=undefined;canvas.width=canvas.height=0;}
          } else if (needsOcr && options.ocr) quality.issues.push('ocr_page_limit');
          else if (needsOcr) quality.issues.push('ocr_available');
          lines=extracted.map(line=>line.text);
          quality.characters=lines.reduce((sum,line)=>sum+line.trim().length,0);
          quality.lines=extracted.map((line,index)=>({line:index+1,box:line.box,fragments:line.fragments,
            source:quality.source==='ocr'?'ocr':'text_layer',
            confidence:'confidence' in line ? line.confidence as number : undefined}));
          if (quality.source==='ocr') {
            quality.readingOrder='geometric_rows';
            quality.columns=1;
          }
        } catch { lines=null;quality.source='error';quality.issues.push('text_extraction_failed'); }
        finally { page.cleanup(); }
      }
      if (consumer.active()) {
        consumer.quality?.(quality);
        await consumer.page(number,lines);
      }
    }
  } finally {
    await dispose();
  }
}
