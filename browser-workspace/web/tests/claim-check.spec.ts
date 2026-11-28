import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const live=Boolean(process.env.DOXA_LIVE_URL);
const start=(live ? '/' : '/dist/')+'#claims';

function longPdf(pages: number) {
  const ref = (n: number) => `${n} 0 R`;
  const nodes = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Count ${pages} /Kids [${Array.from({length:pages},(_,i)=>ref(i+7)).join(' ')}] >>`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
  ];
  for (const body of [
    'BT /F1 10 Tf 40 650 Td (The district publishes records for routine office administration.) Tj ET',
    'BT /F1 10 Tf 40 650 Td (OBSERVATION-PDF-PRIVATE. The study used an observational design.) Tj 0 -18 Td (No stations were randomly assigned and the study had no control group.) Tj ET',
    '',
  ]) nodes.push(`<< /Length ${body.length} >>\nstream\n${body}\nendstream`);
  for (let i=1;i<=pages;i++) nodes.push(
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${ref(i===pages ? 6 : i===pages-1 ? 5 : 4)} >>`);
  let pdf='%PDF-1.4\n';
  const offsets=nodes.map((body,i)=>{
    const offset=pdf.length;
    pdf+=`${i+1} 0 obj\n${body}\nendobj\n`;
    return offset;
  });
  const start=pdf.length;
  pdf+=`xref\n0 ${nodes.length+1}\n0000000000 65535 f \n`+
    offsets.map(offset=>String(offset).padStart(10,'0')+' 00000 n \n').join('')+
    `trailer\n<< /Size ${nodes.length+1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
  return Buffer.from(pdf,'ascii');
}

test('browser verifier finds the exact passage on page 1999 and uploads no document',async ({page,context})=>{
  test.setTimeout(200_000);
  const network: {url:string;method:string;body:string|null}[]=[];
  context.on('request',request=>network.push({url:request.url(),method:request.method(),body:request.postData()}));
  await page.goto(start);
  await page.getByLabel('Source document').setInputFiles({
    name:'OBSERVATION-PDF-PRIVATE.pdf',mimeType:'application/pdf',buffer:longPdf(2000),
  });
  await page.getByRole('textbox',{name:'Claim',exact:true}).fill('The study randomly assigned stations to a control group.');
  await page.getByRole('button',{name:'Check claim'}).click();
  await expect(page.locator('#check-claim-status')).toHaveText('Claim check finished in this browser.',{timeout:175_000});
  await expect(page.getByRole('heading',{name:'Source contradicts the claim'})).toBeVisible();
  await expect(page.locator('#check-claim-coverage')).toContainText('2,000 PDF pages');
  await expect(page.locator('#check-claim-gaps')).toContainText('1 without selectable text');
  await expect(page.locator('#check-claim-evidence')).toContainText('PDF page 1999');
  await expect(page.locator('#check-claim-evidence blockquote').first()).toContainText('No stations were randomly assigned');
  await expect(page.getByRole('link',{name:'Open source page'})).toHaveAttribute('href',/^blob:.*#page=1999$/);
  const files=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download claim report'}).click();
  const packet=JSON.parse(await readFile((await (await files).path())!,'utf8'));
  expect(packet.leads[0].window.page).toBe(1999);
  expect(network.every(request=>request.method==='GET' && request.body===null)).toBe(true);
  expect(JSON.stringify(network)).not.toContain('OBSERVATION-PDF-PRIVATE');
  await page.setViewportSize({width:375,height:830});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  const real=process.env.DOXA_CLAIM_PAPER_FILE;
  if (real) {
    await page.getByLabel('Source document').setInputFiles(real);
    await page.getByRole('textbox',{name:'Claim',exact:true}).fill(
      'The paper describes a machine learning approach to estimate the political leaning of Web domains.');
    await page.getByRole('button',{name:'Check claim'}).click();
    await expect(page.locator('#check-claim-status')).toHaveText('Claim check finished in this browser.',{timeout:60_000});
    await expect(page.getByRole('heading',{name:'Source supports the claim'})).toBeVisible();
    await expect(page.locator('#check-claim-evidence blockquote').first()).toContainText(/political bias|Web domains|political leaning/i);
  }
  if (!live) {
    await page.setViewportSize({width:1100,height:1000});
    await page.screenshot({path:'../release_staging/space/claim-check.png',fullPage:true});
  }
});

test('no lexical candidate returns an unresolved result and avoids the model download',async ({page,context})=>{
  const requests:string[]=[];
  context.on('request',request=>requests.push(request.url()));
  await page.goto(start);
  await page.getByLabel('Source document').setInputFiles({
    name:'text.txt',mimeType:'text/plain',buffer:Buffer.from('The council published a timetable for the public archive.'),
  });
  await page.getByRole('textbox',{name:'Claim',exact:true}).fill('Oranges suppress ultraviolet fluorescence.');
  await page.getByRole('button',{name:'Check claim'}).click();
  await expect(page.getByRole('heading',{name:'Insufficient evidence in the retrieved text'})).toBeVisible();
  await expect(page.locator('#check-claim-gaps')).toContainText('No passage shared searchable words');
  expect(requests.filter(url=>url.includes('model_quint8_avx2.onnx'))).toHaveLength(0);
});
