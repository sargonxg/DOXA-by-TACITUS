import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const hosted=Boolean(process.env.DOXA_LIVE_URL);
const prefix=hosted ? '/' : '/dist/';

// A valid selectable-text PDF with distinct physical pages and a blank last page.
function makePdf(pages: number) {
  const nodes=[
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Count ${pages} /Kids [${Array.from({length:pages},(_,i)=>`${i+6} 0 R`).join(' ')}] >>`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
  ];
  const stream='BT /F1 11 Tf 72 720 Td (Methods) Tj 0 -18 Td (This observational study used 45 participants and routine records.) Tj 0 -18 Td (LOCAL-PAPER-PRIVATE: Covariates may indicate selection bias.) Tj ET';
  nodes.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  nodes.push('<< /Length 0 >>\nstream\n\nendstream');
  for (let i=1;i<=pages;i++) {
    nodes.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${i===pages ? 5 : 4} 0 R >>`);
  }
  const offsets=[0];
  let pdf='%PDF-1.4\n';
  for (const node of nodes) {
    offsets.push(pdf.length);
    pdf+=`${offsets.length-1} 0 obj\n${node}\nendobj\n`;
  }
  const xref=pdf.length;
  pdf+=`xref\n0 ${offsets.length}\n0000000000 65535 f \n`
    + offsets.slice(1).map(offset=>String(offset).padStart(10,'0')+' 00000 n \n').join('')
    + `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf,'ascii');
}

async function report(page: Page) {
  const pending=page.waitForEvent('download');
  await page.getByRole('button',{name:'Export audit data (JSON)'}).click();
  return JSON.parse(await readFile((await (await pending).path())!,'utf8'));
}

test('opens the one audit from the old console link and runs a licensed paper', async ({page})=>{
  await page.goto(prefix+'index.html#console');
  await expect(page.getByRole('heading',{name:'Audit political bias in documents.'})).toBeVisible();
  await expect(page.getByLabel('Source documents')).toBeVisible();
  await expect(page.getByRole('textbox',{name:'Claim',exact:true})).toHaveCount(0);
  await expect(page.getByRole('link',{name:'Conflict Desk'})).toHaveCount(0);
  await page.getByRole('button',{name:'Try a research corpus'}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.',{timeout:60_000});
  await expect(page.locator('#document-coverage')).toContainText('23 of 23 PDF pages');
  await expect(page.getByRole('link',{name:/Rönnback/})).toHaveAttribute('href',/doi.org/);
  await expect(page.getByRole('heading',{name:'Political language flags'})).toBeVisible();
  await expect(page.locator('#document-rates').getByRole('button',{name:'Explain Evaluative framing',exact:true})).toBeVisible();
  await expect(page.locator('#run-analysis')).toContainText('143 of 143 eligible passages screened');
  await expect(page.locator('#run-analysis')).toContainText('111 of 143 checks');
  await page.getByRole('button',{name:'Read measurement and scope',exact:true}).click();
  await expect(page.getByLabel('Criterion or research cue')).toHaveValue(/cue:(measurement_risk|generalizability)/);
  await expect(page.locator('#document-findings > details').first()).toBeVisible();
  await page.getByRole('button',{name:'Read the causal limitation',exact:true}).click();
  await expect(page.locator('#document-coverage')).toContainText('18 of 18 PDF pages');
  await expect(page.locator('#document-findings > details').first()).toBeVisible();
  await expect(page.locator('#document-findings details[open] > blockquote').first()).toContainText('cannot identify causal effects');
  await page.getByRole('button',{name:'Inspect the caption',exact:true}).click();
  await expect(page.locator('#document-findings details[open] > blockquote').first()).toContainText('Overview of data aggregation process');
  await page.getByText('Academic methods and causal claims',{exact:true}).click();
  await expect(page.getByRole('heading',{name:'Research cues'})).toBeVisible();
  await page.getByText('Section coverage',{exact:true}).click();
  await expect(page.getByRole('row',{name:/Methods/}).first()).toBeVisible();
  const packet=await report(page);
  expect(packet.documents.map((item:{progress:{pages:number}})=>item.progress.pages)).toEqual([23,18]);
  expect(packet.documents[0].progress.screened).toBeGreaterThan(0);
  expect(packet.documents[0].supportPassages).toBeUndefined();
  for (const doc of packet.documents) {
    for (const [criterion,counts] of Object.entries(doc.criteria) as [string,Record<string,number>][]) {
      for (const [outcome,total] of Object.entries(counts)) {
        const sum=Object.values(doc.sectionCriteria).reduce((n:number,section:any)=>n+(section[criterion]?.[outcome] ?? 0),0);
        expect(sum).toBe(total);
      }
    }
  }
  await expect(page.locator('#corpus-matrix .low-coverage').first()).toBeVisible();
  await page.getByRole('button',{name:'Inspect Evaluative framing in From hashtags to ballots.pdf'}).click();
  await expect(page.getByLabel('Criterion or research cue')).toHaveValue('model:evaluative_framing');
  await expect(page.locator('#document-coverage')).toContainText('18 of 18 PDF pages');
  await expect(page.locator('#document-findings > details')).toHaveCount(packet.documents[1].criteria.evaluative_framing.present);
  await expect(page.getByRole('option',{name:'Citation marker',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Authors',exact:true}).click();
  await expect(page.getByRole('row',{name:/Schwemmer & Riedl/})).toBeVisible();
  await page.getByRole('button',{name:'Documents',exact:true}).click();
  await page.getByLabel('Inspect document').selectOption('0');
  const sectionAction=page.locator('#section-analysis').getByRole('button',{name:/^Inspect /}).first();
  const sectionName=(await sectionAction.getAttribute('aria-label'))!.split(' in ').at(-1)!;
  await sectionAction.click();
  await expect(page.locator('#section-filter-note')).toBeVisible();
  await expect(page.locator('#criterion-reading')).toContainText(sectionName+' ·');
  await page.getByRole('button',{name:'Show all sections',exact:true}).click();
  await expect(page.locator('#section-filter-note')).toBeHidden();
  if (!hosted) {
    await page.setViewportSize({width:1100,height:1220});
    await page.evaluate(()=>scrollTo(0,document.getElementById('document-results')!.offsetTop-90));
    await page.screenshot({path:'../release_staging/space/document-desk.png'});
  }
  await page.setViewportSize({width:375,height:820});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test('reviews 120 PDF pages with source locations and extraction gaps locally',async ({page,context})=>{
  test.setTimeout(75_000);
  const requests:{url:string;method:string;body:string|null}[]=[];
  context.on('request',request=>requests.push({url:request.url(),method:request.method(),body:request.postData()}));
  await page.goto(prefix+'#documents');
  await page.getByLabel('Source documents').setInputFiles({
    name:'LOCAL-PAPER-PRIVATE.pdf',mimeType:'application/pdf',buffer:makePdf(120),
  });
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.locator('#document-status')).toContainText('Finished with extraction gaps',{timeout:45_000});
  await expect(page.locator('#document-coverage')).toContainText('120 of 120 PDF pages');
  await expect(page.locator('#document-coverage')).toContainText('1 without selectable text');
  await page.getByLabel('Criterion or research cue').selectOption('cue:study_design');
  await page.locator('#document-findings summary').first().click();
  await expect(page.locator('#document-findings mark').first()).toHaveText('observational study');
  await expect(page.getByRole('link',{name:/Open PDF page/}).first()).toHaveAttribute('href',/^blob:.*#page=\d+$/);
  const packet=await report(page);
  expect(packet.progress.pages).toBe(120);
  expect(packet.progress.emptyPages).toBe(1);
  expect(packet.progress.screened).toBe(119);
  expect(requests.every(req=>req.method==='GET'&&req.body===null)).toBe(true);
  expect(JSON.stringify(requests)).not.toContain('LOCAL-PAPER-PRIVATE');
});

test('checks a selected causal flag against separate Methods and Results passages',async ({page,context})=>{
  test.setTimeout(180_000);
  const requests:{url:string;method:string;body:string|null}[]=[];
  context.on('request',request=>requests.push({url:request.url(),method:request.method(),body:request.postData()}));
  await page.goto(prefix);
  const paper=`# Fictional turnout paper

## Abstract
The turnout policy caused the improvement in turnout among residents.

## Methods
The turnout policy caused the improvement
in turnout among residents.

We used an observational study with a convenience sample. The design cannot establish that the turnout policy caused the improvement in turnout among residents. The analysis has a risk of selection bias.

## Results
Turnout among residents rose compared with the previous year. The observational comparison did not establish the effect of the turnout policy.

## Limitations
Potential confounding and missing data may limit generalizability.
`;
  await page.getByLabel('Source documents').setInputFiles({
    name:'LOCAL-PAPER-PRIVATE.md',mimeType:'text/markdown',buffer:Buffer.from(paper),
  });
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.');
  await page.getByText('Academic methods and causal claims',{exact:true}).click();
  await expect(page.locator('#document-academic').getByRole('button',{name:'Explain Sampling or selection',exact:true})).toBeVisible();
  await page.getByLabel('Criterion or research cue').selectOption('claim:direct_without_marker');
  await page.locator('#document-findings summary').first().click();
  await page.getByRole('button',{name:'Check against Methods and Results'}).click();
  await expect(page.getByText(/passages checked in Methods and Results/)).toBeVisible({timeout:150_000});
  const packet=await report(page);
  expect(packet.evidenceChecks).toHaveLength(1);
  expect(packet.evidenceChecks[0].screened).toBeGreaterThan(0);
  expect(packet.evidenceChecks[0].passages).toBe(2);
  expect(packet.evidenceChecks[0].leads[0].window.text).toMatch(/observational/);
  expect(packet.evidenceChecks[0].leads[0].window.text.trim()).not.toEqual(packet.evidenceChecks[0].claim.trim());
  const review=page.locator('#document-findings > details').first();
  await review.getByText('Record a review decision',{exact:true}).first().click();
  await review.getByLabel('Reviewer decision').selectOption('needs_context');
  await review.getByLabel('Reason or next step (optional)').fill('Ask the analyst for the identification strategy. <script>alert(1)</script>');
  await review.getByText('Draft a precise revision',{exact:true}).click();
  await review.getByRole('button',{name:'Start from original',exact:true}).click();
  await expect(review.getByLabel('Proposed wording',{exact:true})).toHaveValue('The turnout policy caused the improvement in turnout among residents.');
  await review.getByLabel('Proposed wording',{exact:true}).fill('Turnout rose after the policy. The observational comparison cannot establish causation.');
  await review.getByLabel('What the revision should improve',{exact:true}).fill('Align the claim with the design while preserving the reported increase.');
  await review.getByLabel('Evidence, uncertainty and attribution preserved',{exact:true}).check();
  await review.getByRole('button',{name:'Save decision'}).click();
  await expect(page.getByRole('heading',{name:'Review decisions'})).toBeVisible();
  const reviewed=await report(page);
  expect(reviewed.reviewDecisions[0].disposition).toBe('needs_context');
  expect(reviewed.reviewDecisions[0].finding.category).toBe('claim');
  expect(reviewed.reviewDecisions[0].finding.text).toBe('The turnout policy caused the improvement in turnout among residents.');
  expect(reviewed.reviewDecisions[0].mitigation.status).toBe('reviewer_draft');
  expect(reviewed.reviewDecisions[0].mitigation.checks.evidence).toBe(true);
  expect(reviewed.evidenceChecks[0].studyPacket.sections.map((item:any)=>item.section)).toEqual(['methods','results']);
  expect(reviewed.evidenceChecks[0].studyPacket.noMatchingPassage).toContain('limitations');
  await page.getByRole('button',{name:'Save project on this browser',exact:true}).click();
  await expect(page.locator('#project-status')).toContainText('revision 1.');
  await review.getByLabel('Reviewer decision').selectOption('dismissed');
  await review.getByRole('button',{name:'Save decision'}).click();
  await expect(page.getByRole('button',{name:/Concern dismissed.*LOCAL-PAPER-PRIVATE/})).toBeVisible();
  await review.getByLabel('Reviewer decision').selectOption('needs_context');
  await review.getByRole('button',{name:'Save decision'}).click();
  page.once('dialog',dialog=>dialog.dismiss());
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.locator('#document-status')).toContainText('Current audit kept.');
  const pending=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download case file (HTML)'}).click();
  const html=await readFile((await (await pending).path())!,'utf8');
  expect(html).toContain(createHash('sha256').update(paper).digest('hex'));
  expect(html).toContain('Automated Methods and Results check');
  expect(html).toContain('Ask the analyst for the identification strategy. &lt;script&gt;');
  expect(html).not.toContain('<script');
  expect(html).toContain('Turnout rose after the policy. The observational comparison cannot establish causation.');
  expect(html).toContain('Reviewer checked · Evidence, uncertainty and attribution preserved');
  await page.getByRole('button',{name:/Needs follow-up.*LOCAL-PAPER-PRIVATE/}).click();
  await expect(page.getByText('Needs follow-up',{exact:true}).first()).toBeVisible();
  await page.locator('.review-editor > summary').first().click();
  await review.getByRole('button',{name:'Clear decision'}).click();
  await expect(page.getByRole('heading',{name:'Review decisions'})).toBeHidden();
  expect(requests.every(req=>req.method==='GET'&&req.body===null)).toBe(true);
  expect(JSON.stringify(requests)).not.toContain('LOCAL-PAPER-PRIVATE');
});

test('keeps usable sources when one PDF fails and stops a run on request',async ({page})=>{
  await page.goto(prefix);
  const source=page.getByLabel('Source documents');
  await source.setInputFiles([
    {name:'Corrupted.pdf',mimeType:'application/pdf',buffer:Buffer.from('This file is damaged.')},
    {name:'Readable.txt',mimeType:'text/plain',buffer:Buffer.from('We conducted an observational study using records from 148 residents each month.')},
  ]);
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.getByRole('heading',{name:'Readable.txt'})).toBeVisible();
  const partial=await report(page);
  expect(partial.failedFiles).toEqual(['Corrupted.pdf']);
  expect(partial.documents).toHaveLength(1);
  expect(partial.partial).toBe(true);
  await source.setInputFiles({name:'Long.pdf',mimeType:'application/pdf',buffer:makePdf(1000)});
  await page.getByRole('button',{name:'Run audit'}).click();
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(page.locator('#document-status')).toContainText('Audit canceled');
  await expect(page.getByRole('button',{name:'Run audit'})).toBeEnabled();
  await source.setInputFiles(Array.from({length:13},(_,i)=>({
    name:`Source ${i+1}.txt`,mimeType:'text/plain',buffer:Buffer.from('A source file.'),
  })));
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.locator('#document-status')).toContainText('Choose up to 12');
});

test('compares a labeled corpus by document and author',async ({page})=>{
  await page.goto(prefix+'#console');
  const passage='The authors reported that the transport committee described the policy as an irresponsible failure. They presented the entire administration as incompetent.';
  await page.getByLabel('Source documents').setInputFiles([
    {name:'Transit report.txt',mimeType:'text/plain',buffer:Buffer.from(passage)},
    {name:'Budget report.txt',mimeType:'text/plain',buffer:Buffer.from(passage+' More text from its conclusion.')},
    {name:'Field memo.txt',mimeType:'text/plain',buffer:Buffer.from('The research team ran an observational study including 148 residents each month.')},
  ]);
  await page.getByText('Label authors (optional)',{exact:true}).click();
  await page.getByLabel('Transit report.txt').fill('Author One');
  await page.getByLabel('Budget report.txt').fill('Author One');
  await page.getByLabel('Field memo.txt').fill('Author Two');
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.getByRole('heading',{name:'Corpus audit'})).toBeVisible();
  await expect(page.locator('#corpus-matrix tbody tr')).toHaveCount(3);
  await page.getByRole('button',{name:'Authors',exact:true}).click();
  await expect(page.locator('#corpus-matrix tbody tr')).toHaveCount(2);
  await expect(page.getByRole('row',{name:/Author One · 2 documents/})).toBeVisible();
  await expect(page.locator('.distribution').first()).toBeVisible();
  await page.getByLabel('Inspect document').selectOption('2');
  await expect(page.locator('#document-coverage')).toContainText('Field memo.txt · Author Two');
  await page.getByLabel('Criterion or research cue').selectOption('cue:study_design');
  await page.locator('#document-findings > details > summary').first().click();
  await page.getByText('Record a review decision',{exact:true}).click();
  await page.getByLabel('Reviewer decision').selectOption('needs_context');
  await page.getByText('Draft a precise revision',{exact:true}).click();
  await page.getByLabel('Proposed wording',{exact:true}).fill('The observational study describes the 148 residents in its sample.');
  await page.getByRole('button',{name:'Save decision'}).click();
  await page.getByLabel('Inspect document').selectOption('0');
  await page.getByRole('button',{name:/Needs follow-up.*Field memo.txt/}).click();
  await page.locator('.review-editor > summary').first().click();
  await page.getByText('Draft a precise revision',{exact:true}).click();
  await expect(page.getByLabel('Proposed wording',{exact:true})).toHaveValue('The observational study describes the 148 residents in its sample.');
  const packet=await report(page);
  expect(packet.documents.map((item:{author:string})=>item.author)).toEqual(['Author One','Author One','Author Two']);
  expect(packet.documents.every((item:{complete:boolean})=>item.complete)).toBe(true);
  expect(packet.documents[2].reviewDecisions[0].finding.text).toBe('The research team ran an observational study including 148 residents each month.');
  await page.setViewportSize({width:375,height:820});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test('explains a criterion by keyboard and supports a direct guide link',async ({page})=>{
  await page.goto(prefix+'#guide/evidential_overclaim');
  const guide=page.getByRole('dialog',{name:'Read the criteria'});
  await expect(guide).toBeVisible();
  await expect(guide.getByRole('heading',{name:'Evidence overclaim',exact:true})).toBeVisible();
  await expect(guide.getByText(/Fictional illustration.*observational association/)).toBeVisible();
  await page.getByRole('button',{name:'Close guide',exact:true}).click();
  await page.getByRole('button',{name:'Try a research corpus'}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.');
  const button=page.locator('#document-rates').getByRole('button',{name:'Explain Evidence overclaim',exact:true});
  await button.focus();
  await expect(button.locator('..').getByRole('tooltip')).toBeVisible();
  await button.press('Enter');
  await expect(guide).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(guide).toBeHidden();
  await page.setViewportSize({width:375,height:820});
  await button.click();
  await expect(guide.getByRole('heading',{name:'Evidence overclaim',exact:true})).toBeVisible();
  expect(await guide.evaluate(node=>node.scrollWidth<=node.clientWidth)).toBe(true);
});

test('keeps exact section counts when model excerpts reach their retention cap',async ({page})=>{
  await page.goto(prefix);
  const passage='The policy is a reckless and disastrous betrayal. The committee is incompetent and its irresponsible agenda is a disgrace to every resident.';
  const text='# Repeated wording fixture\n\n## Introduction\n\n'+(passage+'\n\n').repeat(320)+'## Discussion\n\n'+(passage+'\n\n').repeat(30)+'## Conclusion\n\n'+passage;
  await page.getByLabel('Source documents').setInputFiles({name:'Retention fixture.md',mimeType:'text/markdown',buffer:Buffer.from(text)});
  await page.getByRole('button',{name:'Run audit'}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.',{timeout:30000});
  const packet=await report(page);
  expect(packet.progress.screened).toBe(351);
  const counts=packet.criteria.evaluative_framing;
  expect(counts.present).toBeGreaterThan(240);
  expect(packet.retainedFindings.filter((item:any)=>item.category==='model'&&item.check==='evaluative_framing')).toHaveLength(240);
  expect(packet.sectionCriteria.introduction.evaluative_framing.present+packet.sectionCriteria.discussion.evaluative_framing.present+packet.sectionCriteria.conclusion.evaluative_framing.present).toBe(counts.present);
  await expect(page.locator('#document-sample-note')).toContainText('sampled from the full count');
  expect(packet.sectionCriteria.conclusion.evaluative_framing.present).toBe(1);
  expect(packet.retainedFindings.filter((item:any)=>item.category==='model'&&item.check==='evaluative_framing'&&item.section==='conclusion')).toHaveLength(0);
  await page.getByRole('button',{name:'Inspect Evaluative framing in Conclusion',exact:true}).click();
  await expect(page.locator('#criterion-reading')).toContainText('Conclusion · 1 of 1');
  await expect(page.locator('#document-findings')).toContainText('no excerpt from this section was retained');
  await page.getByRole('button',{name:'Show all sections',exact:true}).click();
  await expect(page.locator('#document-findings > details')).toHaveCount(240);
});

test('selects a licensed research excerpt and preserves its source lineage in the audit',async ({page})=>{
  const index=JSON.parse(await readFile('../research_atlas/stage/research-index.json','utf8'));
  const source=index.sources.find((item:any)=>(!item.language || /^en(?:g|-|$)/i.test(item.language))
    && item.example_ids.some((id:string)=>index.examples.some((p:any)=>p.id===id && /\blimitations?\b/i.test(p.text))));
  const example=index.examples.find((item:any)=>source.example_ids.includes(item.id) && /\blimitations?\b/i.test(item.text));
  if (!hosted) await page.route('**/datasets/Giuliocc/doxa-research-atlas/resolve/v0.1.0/research-index.json',route=>route.fulfill({json:index}));
  await page.goto(prefix+'#guide/evidential_overclaim');
  await page.getByText('Find research and source passages',{exact:true}).click();
  await expect(page.locator('#research-status')).toContainText(index.counts.documents.toLocaleString(),{timeout:30000});
  await page.getByLabel('Research question',{exact:true}).selectOption('comparative_double_standard');
  await expect(page.locator('#research-definition')).toContainText('research only');
  await page.getByLabel('Research question',{exact:true}).selectOption('');
  await page.getByLabel('Sources with a licensed example',{exact:true}).check();
  await page.getByLabel('Search titles and authors',{exact:true}).fill(source.title);
  const card=page.locator('#research-results article').filter({has:page.getByRole('link',{name:source.title,exact:true})});
  await expect(card).toHaveCount(1);
  const excerpt=card.locator('details').filter({has:page.getByText('Licensed example · '+example.source_section+' paragraph '+example.paragraph_index,{exact:true})});
  await excerpt.locator('summary').click();
  expect(await excerpt.locator('blockquote').textContent()).toBe(example.text);
  await excerpt.getByRole('button',{name:'Audit this excerpt',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Read the criteria'})).toBeHidden();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.');
  await expect(page.locator('#document-coverage')).toContainText('Research excerpt');
  const packet=await report(page);
  expect(packet.researchSource.documentId).toBe(source.id);
  expect(packet.researchSource.passageId).toBe(example.id);
  expect(packet.researchSource.textSha256).toBe(createHash('sha256').update(example.text).digest('hex'));
  expect(packet.researchSource.sourceXPath).toBe(example.source_xpath);
  expect(packet.progress.screened).toBeGreaterThan(0);
  await page.getByLabel('Criterion or research cue').selectOption('cue:disclosed_limitation');
  const finding=page.locator('#document-findings > details').first();
  await finding.locator(':scope > summary').click();
  await finding.getByText('Record a review decision',{exact:true}).click();
  await finding.getByLabel('Reviewer decision').selectOption('needs_context');
  await finding.getByRole('button',{name:'Save decision',exact:true}).click();
  const pending=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download case file (HTML)',exact:true}).click();
  const html=await readFile((await (await pending).path())!,'utf8');
  expect(html).toContain(example.source_xpath);
  expect(html).toContain(example.text_sha256);
  expect(html).toContain('Selected research excerpt. Read the full article for context.');
});

test('saves, reopens and transfers a review project with exact reviewer evidence',async ({page,context})=>{
  test.setTimeout(75000);
  const requests:{method:string;body:string|null}[]=[];
  context.on('request',request=>requests.push({method:request.method(),body:request.postData()}));
  await page.goto(prefix);
  const original='The policy is a reckless and disastrous betrayal. The committee is incompetent and its irresponsible agenda is a disgrace to every resident.';
  await page.getByLabel('Source documents').setInputFiles({name:'Project review.md',mimeType:'text/markdown',buffer:Buffer.from(original)});
  await page.getByRole('button',{name:'Run audit',exact:true}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.');
  const finding=page.locator('#document-findings > details').first();
  await finding.locator(':scope > summary').click();
  await finding.getByText('Record a review decision',{exact:true}).click();
  await finding.getByText('Record speaker and evidence',{exact:true}).click();
  await finding.getByLabel('Speaker or attributed source (reviewer supplied)').fill('Report author');
  await finding.getByLabel('Actor, group or policy discussed (reviewer supplied)').fill('Transport committee');
  const source=finding.getByLabel('Original passage (select evidence)');
  await source.evaluate((node:HTMLTextAreaElement)=>{node.setSelectionRange(14,22);node.dispatchEvent(new Event('select'));});
  await finding.getByRole('button',{name:'Add supporting selection',exact:true}).click();
  await finding.getByLabel('Reviewer decision',{exact:true}).selectOption('needs_context');
  await finding.getByText('Draft a precise revision',{exact:true}).click();
  await finding.getByLabel('Proposed wording',{exact:true}).fill('The committee did not approve the policy for 40 residents.');
  await expect(finding.locator('.meaning-review')).toContainText('Negation');
  await finding.getByRole('button',{name:'Save decision',exact:true}).click();
  await page.getByRole('button',{name:'Save project on this browser',exact:true}).click();
  await expect(page.locator('#project-status')).toContainText('revision 1.');
  await page.reload();
  await page.getByText('Open, import or back up a review project',{exact:true}).click();
  await page.getByRole('button',{name:'Open Project review.md',exact:true}).click();
  await expect(page.locator('#project-status')).toContainText('Opened Project review.md');
  const packet=await report(page);
  expect(packet.reviewDecisions[0].annotations.speaker).toBe('Report author');
  expect(packet.reviewDecisions[0].annotations.evidence[0].text).toBe(original.slice(14,22));
  expect(packet.reviewDecisions[0].finding.text).toBe(original);
  expect(packet.reviewActivity.humanTimeSaved).toBeNull();
  const pending=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download project (.doxa)',exact:true}).click();
  const archive=await readFile((await(await pending).path())!);
  await page.reload();
  await page.getByText('Open, import or back up a review project',{exact:true}).click();
  await page.getByLabel('Open a project file',{exact:true}).setInputFiles({name:'Review.doxa',mimeType:'application/vnd.tacitus.doxa-project',buffer:archive});
  await expect(page.locator('#project-status')).toContainText('Opened the archive as an analysis snapshot');
  expect((await report(page)).reviewDecisions[0].annotations.evidence[0].text).toBe(original.slice(14,22));
  expect(requests.every(request=>request.method==='GET' && request.body===null)).toBe(true);
});

test('uses explicit comparison context and excludes unmatched documents from a matched view',async ({page})=>{
  await page.goto(prefix);
  await page.getByLabel('Source documents').setInputFiles([
    {name:'First.txt',mimeType:'text/plain',buffer:Buffer.from('The committee described the policy as a reckless and disastrous failure affecting residents.')},
    {name:'Second.txt',mimeType:'text/plain',buffer:Buffer.from('A different committee explained that the transport policy had changed during the previous year.')},
    {name:'Third.txt',mimeType:'text/plain',buffer:Buffer.from('The research team used a study of 45 residents to describe the budgeting process.')},
  ]);
  await page.getByRole('button',{name:'Run audit',exact:true}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.');
  await expect(page.locator('#comparison-scope-note')).toContainText('0 of 3');
  await page.getByText('Set the comparison context',{exact:true}).click();
  for (const [field,value] of Object.entries({event:'Transit vote',topic:'Transport',genre:'Committee report',period:'2025'})) await page.locator('#comparison-'+field).fill(value);
  await page.getByRole('button',{name:'Apply to this document',exact:true}).click();
  await page.getByLabel('Inspect document',{exact:true}).selectOption('1');
  for (const [field,value] of Object.entries({event:'Transit vote',topic:'Transport',genre:'Committee report',period:'2025'})) await page.locator('#comparison-'+field).fill(value);
  await page.getByRole('button',{name:'Apply to this document',exact:true}).click();
  await expect(page.locator('#comparison-cohorts')).toContainText('2 of 3');
  await page.getByLabel('Comparison scope',{exact:true}).selectOption({index:1});
  await expect(page.locator('#corpus-matrix tbody tr')).toHaveCount(2);
  await expect(page.getByLabel('Inspect document',{exact:true}).locator('option')).toHaveCount(2);
  const packet=await report(page);
  expect(packet.comparisons.matchedDocumentCount).toBe(2);
  expect(packet.comparisons.unmatched[0].label).toBe('Third.txt');
});

test('recognizes a scanned PDF locally and retains reading provenance in a saved project',async ({page,context})=>{
  test.setTimeout(60000);
  const requests:{url:string;method:string;body:string|null}[]=[];
  context.on('request',request=>requests.push({url:request.url(),method:request.method(),body:request.postData()}));
  await page.goto(prefix);
  await page.getByLabel('Source documents').setInputFiles({name:'LOCAL-SCAN-PRIVATE.pdf',mimeType:'application/pdf',buffer:await readFile('../improvement_release/pdf/fixtures/scanned.pdf')});
  await page.getByLabel('Recognize scanned pages locally with English OCR',{exact:true}).check();
  await page.getByRole('button',{name:'Run audit',exact:true}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.',{timeout:45000});
  await page.getByText('PDF reading coverage and layout',{exact:true}).click();
  await expect(page.locator('#source-quality-content')).toContainText('1 recognized by OCR');
  const packet=await report(page);
  expect(packet.progress.emptyPages).toBe(0);
  expect(packet.sourceQuality[0].source).toBe('ocr');
  expect(packet.sourceQuality[0].nativeCharacters).toBe(0);
  expect(packet.sourceQuality[0].characters).toBeGreaterThan(100);
  expect(packet.sourceQuality[0].geometryScope).toBe('retained_findings');
  await page.getByRole('button',{name:'Save project on this browser',exact:true}).click();
  await expect(page.locator('#project-status')).toContainText('revision 1.');
  expect(requests.every(request=>request.method==='GET' && request.body===null)).toBe(true);
  expect(JSON.stringify(requests)).not.toContain('LOCAL-SCAN-PRIVATE');
});

test('runs the published contextual wording model and preserves its version in a project',async ({page,context})=>{
  test.setTimeout(150000);
  const requests:{url:string;method:string;body:string|null}[]=[];
  context.on('request',request=>requests.push({url:request.url(),method:request.method(),body:request.postData()}));
  await page.goto(prefix);
  await page.getByLabel('Source documents').setInputFiles({name:'LOCAL-WORDING-PRIVATE.txt',mimeType:'text/plain',buffer:Buffer.from('The committee’s reckless and disgraceful policy betrayed every resident. Its irresponsible proposal was a disastrous failure for the whole city.')});
  await page.getByRole('button',{name:'Run audit',exact:true}).click();
  await expect(page.locator('#document-status')).toHaveText('Review finished locally.');
  const finding=page.locator('#document-findings > details').first();
  await finding.locator(':scope > summary').click();
  await finding.getByText('Check wording with the contextual model',{exact:true}).click();
  await finding.getByRole('button',{name:'Run wording check',exact:true}).click();
  await expect(finding.locator('.wording-check')).toContainText('Wording check finished locally.',{timeout:120000});
  await expect(finding.locator('.wording-check')).toContainText('Academic and other genres require separate validation.');
  const packet=await report(page);
  expect(packet.wordingChecks).toHaveLength(1);
  expect(packet.wordingChecks[0].method).toBe('doxa-wording-minilm-v1');
  expect(packet.wordingChecks[0].model_revision).toMatch(/^[a-f0-9]{40}$/);
  expect(packet.wordingChecks[0].model_score).toBeGreaterThanOrEqual(0);
  expect(packet.wordingChecks[0].context_used).toBe(false);
  await page.getByRole('button',{name:'Save project on this browser',exact:true}).click();
  await expect(page.locator('#project-status')).toContainText('revision 1.');
  await page.reload();
  await page.getByText('Open, import or back up a review project',{exact:true}).click();
  await page.getByRole('button',{name:'Open LOCAL-WORDING-PRIVATE.txt',exact:true}).click();
  await expect(page.locator('#project-status')).toContainText('Opened LOCAL-WORDING-PRIVATE.txt');
  expect((await report(page)).wordingChecks[0].model_revision).toBe(packet.wordingChecks[0].model_revision);
  expect(requests.every(request=>request.method==='GET' && request.body===null)).toBe(true);
  expect(requests.filter(request=>!request.url.startsWith(page.url().split('/').slice(0,3).join('/'))).every(request=>!request.url.includes('LOCAL-WORDING-PRIVATE'))).toBe(true);
});
