import { expect, test } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';
const hosted = Boolean(process.env.DOXA_LIVE_URL);
const prefix = hosted ? '/' : '/dist/';

async function tool(page: import('@playwright/test').Page, label: string) {
  await page.locator('#workspace-menu > summary').click();
  await page.getByRole('link',{name:label,exact:true}).click();
}

test('conversation runs the model, compares context and keeps pasted text local', async ({page, context}) => {
  const requests: Array<{url:string; method:string; body:string | null}> = [];
  context.on('request', request => requests.push({url:request.url(),method:request.method(),body:request.postData()}));
  await page.goto(prefix);
  await expect(page.getByRole('heading',{name:'Ask DOXA.'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Review political language in context'})).not.toBeVisible();
  if (!hosted) {
    await mkdir('../release_staging/space',{recursive:true});
    await page.screenshot({path:'../release_staging/space/screenshot.png'});
  }
  await page.getByRole('button',{name:'Try an example',exact:true}).click();
  await page.getByRole('button',{name:'Quotation or endorsement?'}).click();
  await expect(page.locator('#desk-log [data-result=screened]')).toHaveCount(1);
  await page.getByRole('button',{name:'Run with supplied context'}).click();
  await expect(page.locator('#desk-log [data-result=screened]')).toHaveCount(2);
  await expect(page.locator('#desk-log [data-result=screened]').last()).toContainText('used the supplied context');
  await page.getByRole('button',{name:'Show the annotated evidence'}).last().click();
  await expect(page.locator('#desk-log blockquote').last()).toContainText('reporting the incident');
  await page.getByRole('button',{name:'Clear conversation'}).click();
  await expect(page.locator('#desk-log article')).toHaveCount(1);
  const input=page.getByRole('textbox',{name:'Message to DOXA'});
  await input.fill('The minister’s statement urged review of the vote. PRIVATE-DESK-CHECK');
  await input.press('Enter');
  await expect(page.locator('#desk-log [data-result=screened]')).toHaveCount(1);
  await page.getByRole('button',{name:'Add my context'}).click();
  await page.getByRole('textbox',{name:'Context to add'}).fill('PRIVATE-CONTEXT: The speaker was quoting the official minutes.');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.locator('#desk-log [data-result=screened]')).toHaveCount(2);
  await expect(page.getByRole('navigation',{name:'Lab tools'})).not.toBeVisible();
  await page.setViewportSize({width:375,height:812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  expect(requests.every(request=>request.method==='GET' && request.body===null)).toBe(true);
  expect(JSON.stringify(requests)).not.toContain('PRIVATE-');
  await page.getByRole('button',{name:'Clear conversation'}).click();
  await expect(page.locator('#desk-log')).not.toContainText('PRIVATE-');
});

test('guided conflict case shows reporting language and preserves the source in the wording review',async ({page})=>{
  await page.goto(prefix);
  await page.getByRole('button',{name:'Try an example',exact:true}).click();
  await page.getByRole('button',{name:'A claim in conflict reporting',exact:true}).click();
  const result=page.locator('#desk-log [data-result=screened]').last();
  await result.getByText('Inspect causal wording (1)',{exact:true}).click();
  await expect(result).toContainText('reporting or quotation nearby');
  await expect(result.locator('blockquote')).toContainText('said that the checkpoint closures caused');
  await result.getByRole('button',{name:'Run with supplied context'}).click();
  const contextual=page.locator('#desk-log [data-result=screened]').nth(1);
  await contextual.getByRole('button',{name:'Show the annotated evidence'}).click();
  await expect(page.locator('#desk-log blockquote').last()).toHaveText('The district administration said that');
  await page.getByRole('button',{name:'Compare an authored revision'}).click();
  await expect(page.getByLabel('Draft wording')).toHaveValue(/A logistics report also recorded time lost to a bridge inspection\./);
});

test('guided workflow, word review and exact reference highlights', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(prefix+'#start');
  await expect(page.getByRole('heading', { name: 'Review political language in context' })).toBeVisible();
  await page.getByRole('button', { name: 'Authored revision' }).click();
  await expect(page.locator('#sample-content')).toContainText('call on the cabinet to answer');
  await page.getByRole('button', { name: 'Original passage' }).click();
  await expect(page.locator('#sample-content mark')).toContainText('Every Jewish person');
  await page.getByRole('button',{ name: 'Try the guided review' }).click();
  await expect(page.getByRole('heading',{ name: 'Live model screening' })).toBeVisible();
  await expect(page.locator('#live-results article')).toHaveCount(8);
  await page.getByRole('button',{ name: 'Add supplied context' }).click();
  await expect(page.getByRole('status').filter({hasText:'Screening finished'})).toBeVisible();
  await page.getByText('Inspect the authored reference').click();
  await expect(page.getByRole('blockquote').filter({ hasText: 'Every Jewish person should answer for it.' }).first()).toBeVisible();
  await page.getByRole('link',{name:'Review the wording'}).click();
  await page.getByRole('button',{name:'Compare wording'}).click();
  await expect(page.getByRole('heading',{name:'Wording changes'})).toBeVisible();
  await page.goto(prefix + '#explore/shared-draft-a');
  await expect(page.locator('#reference-detail mark')).toContainText('All Jewish residents');
  await page.getByRole('combobox', {name:'Criterion', exact:true}).selectOption('group_conspiracy');
  await expect(page.getByRole('region',{name:'Evidence explorer'})).toContainText('judgments');
  await tool(page,'Results');
  await expect(page.getByText('The model handled 0 of 6 expected directional changes.',{exact:false})).toBeVisible();
  expect(errors).toEqual([]);
});

test('arbitrary passages and local CSV import never transmit input', async ({ page, context }) => {
  const requests: Array<{url:string; method:string; body:string | null; redirect?:string}> = [];
  context.on('request', request => requests.push({
    url: request.url(), method: request.method(), body: request.postData(),
    redirect: request.redirectedFrom()?.url(),
  }));
  await page.goto(prefix + '#analyze');
  const assetBase = new URL(prefix, page.url()).href;
  await expect(page.getByRole('heading',{name:'Analyze a passage'})).toBeVisible();
  const passage = page.getByLabel('Passage', {exact:true});
  const screen = page.getByRole('button',{name:'Run local screening'});
  const values = [
    'The cabinet’s plan removes the gate restriction and merits support. PRIVATE-FIXTURE-A',
    'Residents must answer for the policy; nobody cited evidence. PRIVATE-FIXTURE-B',
    'The audit compares two groups and reports different conditions. PRIVATE-FIXTURE-C',
  ];
  const observed = [];
  for (const value of values) {
    await passage.fill(value);
    const started = performance.now();
    await screen.click();
    await expect(page.locator('#analysis-status')).toHaveText('Screening finished in this browser.');
    observed.push({ time: performance.now() - started, result: await page.locator('#live-results').innerText() });
  }
  expect(new Set(observed.map(value => value.result)).size).toBe(3);
  console.log('End-to-end screening interaction milliseconds', observed.map(value => Math.round(value.time)));
  await passage.fill('𐄂𐄂𐄂𐄂𐄂𐄂');
  await screen.click();
  await expect(page.locator('#live-results')).toContainText('No known features');
  await expect(page.locator('#live-results')).not.toContainText('Suggested priority P0');
  await passage.fill('x'.repeat(6001));
  await screen.click();
  await expect(page.locator('#analysis-status')).toContainText('at most 6,000');
  await passage.fill('🌍 The council should release the study.');
  await passage.evaluate((node: HTMLTextAreaElement) => { node.focus(); node.setSelectionRange(0,2); });
  await page.getByRole('button',{name:'Record selection'}).click();
  await expect(page.locator('#reviewer-evidence')).toContainText('passage 0–1: “🌍”');
  await tool(page,'Corpus review');
  const exampleDownload = page.waitForEvent('download');
  await page.getByRole('button', {name:'Download example CSV'}).click();
  expect(await readFile((await (await exampleDownload).path())!, 'utf-8')).toContain('Fictional example');
  await page.getByLabel('Import a CSV or JSONL').setInputFiles({
    name:'private.csv', mimeType:'text/csv',
    buffer:Buffer.from('record_id,document_id,thread_id,text,context\r\n=PRIVATE,doc-a,thread-1,"PRIVATE-FIXTURE-A, a reported closure",\r\nsecond,doc-b,thread-1,"PRIVATE-FIXTURE-A, a reported closure",\r\nbad,doc-c,,"",\r\n'),
  });
  await expect(page.locator('#corpus-preview')).toContainText('Previewing 3 of 3 records');
  await page.getByRole('button',{name:'Review corpus locally'}).click();
  await expect(page.locator('#corpus-status')).toHaveText('Processed 3 of 3 records locally.');
  await expect(page.locator('#corpus-table')).toContainText('Text and context duplicate an earlier record.');
  await page.getByRole('button',{name:'Inspect =PRIVATE'}).click();
  const firstReview = page.locator('#inspection-content article').filter({has:page.getByText('Human disposition')}).first();
  await firstReview.getByRole('combobox',{name:'Human disposition'}).selectOption('dismiss');
  await firstReview.getByRole('button',{name:'Save local review'}).click();
  await expect(firstReview.getByText('Review saved locally.')).toBeVisible();
  await page.getByRole('button',{name:'Close'}).click();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button',{name:'Export CSV'}).click();
  const download = await downloadPromise;
  const output = await readFile((await download.path())!, 'utf8');
  expect(output).toContain('"\'=PRIVATE"');
  expect(output).toContain('dismiss');
  expect(output).toContain('doxa-routing-v0.1');
  const jsonDownload = page.waitForEvent('download');
  await page.getByRole('button',{name:'Export JSONL'}).click();
  const downloadedJson = await jsonDownload;
  const exported = (await readFile((await downloadedJson.path())!, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  expect(exported[0].review_policy_version).toBe('doxa-routing-v0.1');
  expect(exported[0].assessment.model_revision).toMatch(/^local-sha256:/);
  expect(exported[1].duplicate_of).toBe('=PRIVATE');
  await page.getByRole('button',{name:'Reset corpus'}).click();
  await expect(page.locator('#corpus-preview')).toBeEmpty();
  const publicBinaryAssets = ['model/coefficients.f32', 'brand/doxa-reader.webp'];
  const unexpected = requests.filter(r => {
    const allowedRedirect = hosted && publicBinaryAssets.some(name => r.redirect === assetBase + name)
      && new URL(r.url).hostname.endsWith('.cdn.hf.co');
    return !(r.method === 'GET' && r.body === null && (r.url.startsWith(assetBase) || allowedRedirect));
  });
  expect(unexpected.map(r => ({ origin: new URL(r.url).origin, method: r.method,
    redirectedFrom: r.redirect ? new URL(r.redirect).pathname : undefined, hasBody: r.body !== null }))).toEqual([]);
  expect(JSON.stringify(requests)).not.toContain('PRIVATE');
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({local:0,session:0});
});

test('small-screen keyboard flow and corpus cancellation', async ({ page }) => {
  await page.setViewportSize({ width: 375,height: 812 });
  await page.goto(prefix);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link',{name:'Skip to content'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
  await page.locator('#workspace-menu > summary').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('link',{name:'Corpus review', exact:true}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading',{name:'Corpus review'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.getByLabel('Import a CSV or JSONL').setInputFiles({
    name:'cancel.jsonl', mimeType:'application/x-ndjson',
    buffer:Buffer.from(Array.from({length:200},(_,index) => JSON.stringify({
      record_id: String(index), text: 'Fictional review draft about a cabinet vote '.repeat(15) + String(index),
    })).join('\n')),
  });
  await page.getByRole('button',{name:'Review corpus locally'}).click();
  await page.getByRole('button',{name:'Cancel processing'}).click();
  await expect(page.locator('#corpus-status')).toContainText('Canceled after');
  await expect(page.locator('#corpus-summary')).not.toContainText('200 / 200Processed records');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});

test('pixel Conflict Desk reconciles archive rows and checks exact source claims', async ({page}) => {
  await page.goto(prefix + '#conflict');
  await expect(page.getByRole('heading',{name:'Conflict desk'})).toBeVisible();
  await expect(page.locator('#archive-summary')).toContainText('15,750,700 coded records');
  await expect(page.locator('#archive-counts')).toContainText('15,750,700 coded records in this selection');
  await page.getByLabel('GDELT archive month').selectOption('202403');
  await expect(page.locator('#archive-counts')).toContainText('4,032,913 coded records in this selection');
  await page.getByLabel('Legislative source').selectOption('gaza-oversight');
  await expect(page.locator('#archive-evidence mark')).toContainText('third party monitoring');
  await page.getByLabel('Claim to review').selectOption('gaza-oversight-insufficient_context');
  await expect(page.locator('#archive-evidence')).toContainText('Needs additional evidence');
  await expect(page.locator('#archive-evidence mark')).toHaveCount(0);
  await expect(page.getByRole('link',{name:/Read the original on GovInfo/})).toHaveAttribute('href',/govinfo\.gov/);
  const packetDownload = page.waitForEvent('download');
  await page.getByRole('button',{name:'Download model input'}).click();
  const packet = JSON.parse(await readFile((await (await packetDownload).path())!, 'utf8'));
  expect(packet.passage).toContain('third party monitoring');
  expect(Object.keys(packet).sort()).toEqual(['claim','passage']);
  const mascot = page.locator('#conflict .pixel-reader');
  await expect.poll(() => mascot.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('#experiment-score')).toContainText('0.675 macro-F1');
  await expect(page.getByRole('link',{name:'Download scores and predictions'})).toBeVisible();
  if (!hosted) {
    await page.screenshot({path:'../release_staging/space/conflict-desk.png',fullPage:false});
  }
});
