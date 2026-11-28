import { createWorker, OEM, PSM } from 'tesseract.js';
import type { PdfLayoutLine } from './pdf-layout';

/** A local English OCR worker. Source images travel through browser messages. */
export async function startPdfOcr(progress?: (text: string) => void) {
  const base=new URL('./ocr/',document.baseURI);
  const worker=await createWorker('eng',OEM.LSTM_ONLY,{
    workerPath:new URL('worker.min.js',base).href,
    corePath:new URL('core/',base).href,
    langPath:new URL('lang/',base).href,
    workerBlobURL:false,
    logger:message=>{
      if (message.status==='recognizing text') progress?.('Reading scanned text locally · '+Math.round(message.progress*100)+'%');
      else progress?.('Preparing local English OCR…');
    },
  });
  await worker.setParameters({tessedit_pageseg_mode:PSM.AUTO,user_defined_dpi:'144',preserve_interword_spaces:'1'});
  return {
    async recognize(canvas: HTMLCanvasElement, scale: number) {
      const result=await worker.recognize(canvas,{}, {text:true,blocks:true});
      const recognized=result.data.blocks?.flatMap(block=>block.paragraphs.flatMap(paragraph=>paragraph.lines)) ?? [];
      const lines: (PdfLayoutLine & {confidence:number})[]=recognized.filter(line=>line.text.trim()).map(line=>({
        text:line.text.trimEnd(),
        box:[line.bbox.x0/scale,line.bbox.y0/scale,(line.bbox.x1-line.bbox.x0)/scale,(line.bbox.y1-line.bbox.y0)/scale],
        fragments:[],fontSize:(line.bbox.y1-line.bbox.y0)/scale,confidence:line.confidence,
      }));
      return {lines,confidence:result.data.confidence};
    },
    terminate:()=>worker.terminate(),
  };
}
