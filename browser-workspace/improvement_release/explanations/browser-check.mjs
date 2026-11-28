import assert from 'node:assert/strict';
import { chromium } from '../../web/node_modules/playwright/index.mjs';
import { expect } from '../../web/node_modules/@playwright/test/index.mjs';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync, spawn } from 'node:child_process';

const temporary = await mkdtemp(join(tmpdir(), 'doxa-reading-'));
const checks = [];
let server;
let browser;
const source = '🗳️ Mayor Élodie Renard said, “The 48 residents may oppose the policy.”\r\nThe report records this position.';
const finding = {
  category: 'model', check: 'evaluative_framing', text: source,
  context: 'A fictional record authored for engineering tests.', section: 'discussion',
  passage: 7, firstLine: 3, lastLine: 5, score: 0.9,
};

try {
  execFileSync(new URL('../../web/node_modules/.bin/tsc', import.meta.url).pathname, [
    '--strict', '--target', 'ES2022', '--module', 'ESNext',
    '--moduleResolution', 'Bundler', '--skipLibCheck', '--outDir', temporary,
    new URL('../../web/src/finding-reading.ts', import.meta.url).pathname,
  ], { stdio: 'pipe' });
  for (const name of ['finding-explanation', 'finding-reading']) {
    const input = await readFile(join(temporary, name + '.js'), 'utf8');
    const compiled = input.replace(/(from\s+['"]\.\/finding-explanation)(['"])/g, '$1.js$2');
    await writeFile(join(temporary, name + '.js'), compiled);
  }
  await writeFile(join(temporary, 'index.html'), '<!doctype html><html lang="en"><meta charset="utf-8"><title>DOXA reading fixture</title><body></body></html>');
  server = spawn('python3', ['-m', 'http.server', '8169', '--bind', '127.0.0.1', '--directory', temporary], { stdio: 'ignore' });
  const base = 'http://127.0.0.1:8169';
  let ready = false;
  for (let attempt = 0; attempt < 30; attempt++) {
    try { ready = (await fetch(base)).ok; } catch { /* Server startup. */ }
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  assert(ready, 'The isolated fixture server must start');
  browser = await chromium.launch({ executablePath: '/usr/bin/chromium' });
  const page = await browser.newPage();
  await page.goto(base);
  await page.evaluate(async finding => {
    const api = await import('./finding-reading.js');
    window.readingApi = api;
    window.finding = Object.freeze(finding);
    window.readingComponent = api.createFindingReading(window.finding);
    document.body.append(window.readingComponent.element);
    const second = api.createFindingReading(window.finding);
    const firstIds = Array.from(window.readingComponent.element.querySelectorAll('[id]'), node => node.id);
    const secondIds = Array.from(second.element.querySelectorAll('[id]'), node => node.id);
    window.componentIds = [...firstIds, ...secondIds];
  }, finding);
  assert.equal(await page.evaluate(() => window.readingComponent.readAnnotations()), undefined);
  const ids = await page.evaluate(() => window.componentIds);
  assert.equal(new Set(ids).size, ids.length);
  const cuesAreAnchored = await page.evaluate(() => Array.from(document.querySelectorAll('.reading-cues mark'))
    .every(mark => window.finding.text.slice(Number(mark.dataset.start), Number(mark.dataset.end)) === mark.textContent));
  assert(cuesAreAnchored);
  checks.push('empty entries, unique labels and exact source cues');

  await page.getByText('Record speaker and evidence', { exact: true }).click();
  await page.getByLabel('Speaker or attributed source (reviewer supplied)', { exact: true }).fill('Mayor Élodie Renard');
  await page.getByLabel('Actor, group or policy discussed (reviewer supplied)', { exact: true }).fill('The council policy');
  await page.getByLabel('Scope, attribution or context note', { exact: true }).fill('The quoted speaker expresses a position.');
  const selector = page.getByLabel('Original passage (select evidence)', { exact: true });
  await expect(selector).toHaveJSProperty('readOnly', true);
  await selector.focus();
  await selector.press('Control+a');
  const supporting = page.getByRole('button', { name: 'Add supporting selection', exact: true });
  await expect(supporting).toBeEnabled();
  await selector.press('Tab');
  await expect(supporting).toBeFocused();
  await page.keyboard.press('Enter');
  let annotations = await page.evaluate(() => window.readingComponent.readAnnotations());
  assert.equal(annotations.provenance, 'reviewer_entered');
  assert.equal(annotations.speaker, 'Mayor Élodie Renard');
  assert.equal(annotations.evidence[0].text, source);
  assert.equal(annotations.evidence[0].start, 0);
  assert.equal(annotations.evidence[0].end, source.length);
  assert.equal(await page.evaluate(() => window.finding.text), source);
  await supporting.click();
  assert.equal((await page.evaluate(() => window.readingComponent.readAnnotations())).evidence.length, 1);
  checks.push('keyboard selection preserves Unicode, CRLF source and reviewer entries');

  await selector.evaluate(element => {
    element.setSelectionRange(1, 2);
    element.dispatchEvent(new Event('select'));
  });
  await expect(supporting).toBeDisabled();
  await selector.evaluate(element => {
    element.setSelectionRange(3, 4);
    element.dispatchEvent(new Event('select'));
  });
  await expect(supporting).toBeDisabled();
  await selector.focus();
  await selector.press('Control+a');
  await page.getByRole('button', { name: 'Add countervailing selection', exact: true }).click();
  annotations = await page.evaluate(() => window.readingComponent.readAnnotations());
  assert.equal(annotations.evidence.length, 2);
  assert.equal(annotations.evidence[1].role, 'countervailing');
  await page.getByRole('button', { name: 'Remove supporting evidence 1', exact: true }).click();
  assert.equal((await page.evaluate(() => window.readingComponent.readAnnotations())).evidence.length, 1);
  await page.getByRole('button', { name: 'Remove countervailing evidence 1', exact: true }).click();
  assert.equal((await page.evaluate(() => window.readingComponent.readAnnotations())).evidence.length, 0);
  checks.push('surrogate boundaries, empty selections, duplicate spans, evidence roles and removal');

  const restored = {
    provenance: 'reviewer_entered', speaker: 'Mayor Élodie Renard', target: '', scopeNote: '',
    evidence: [{ role: 'supporting', start: 4, end: 9, text: source.slice(4, 9) }],
  };
  const restoration = await page.evaluate(existing => {
    const before = JSON.stringify(existing);
    const component = window.readingApi.createFindingReading(window.finding, existing);
    const first = component.readAnnotations();
    first.evidence[0].text = 'Altered caller copy';
    const second = component.readAnnotations();
    const invalid = window.readingApi.createFindingReading(window.finding, {
      provenance: 'reviewer_entered', speaker: '', target: '', scopeNote: '',
      evidence: [{ role: 'supporting', start: 0, end: 4, text: 'Wrong source' }],
    });
    return { unchangedInput: JSON.stringify(existing) === before, second,
      invalid: invalid.readAnnotations(), invalidMessage: invalid.element.textContent };
  }, restored);
  assert(restoration.unchangedInput);
  assert.equal(restoration.second.evidence[0].text, restored.evidence[0].text);
  assert.equal(restoration.invalid, undefined);
  assert(restoration.invalidMessage.includes('do not match this source passage'));
  checks.push('restored annotations retain source validation and independent copies');

  await page.evaluate(() => {
    const malicious = { ...window.finding, text: 'The witness said, “<img src=x onerror=window.doxaInjected=true>.”' };
    const component = window.readingApi.createFindingReading(malicious);
    document.body.append(component.element);
  });
  assert.equal(await page.locator('img').count(), 0);
  assert.equal(await page.evaluate(() => window.doxaInjected), undefined);
  checks.push('source strings render as text without HTML execution');

  const meaning = await page.evaluate(() => {
    const element = window.readingApi.renderMitigationReview(
      'Mayor Alice said, “The 48 residents may not approve the policy.”',
      'Mayor Peter said, “The 84 residents approve the policy.”');
    document.body.append(element);
    return { status: element.dataset.status, changes: element.querySelectorAll('.meaning-changes details').length, text: element.textContent };
  });
  assert.equal(meaning.status, 'review_required');
  assert.equal(meaning.changes, 5);
  assert(meaning.text.includes('48') && meaning.text.includes('84'));
  const sameTerms = await page.evaluate(() => {
    const element = window.readingApi.renderMitigationReview('Alice blamed Bob.', 'Bob blamed Alice.');
    const empty = window.readingApi.renderMitigationReview('A supported criticism.', '');
    return { status: element.dataset.status, text: element.textContent, emptyStatus: empty.dataset.status, emptyText: empty.textContent };
  });
  assert.equal(sameTerms.status, 'review_required');
  assert(sameTerms.text.includes('Check the propositions, actor roles and political meaning'));
  assert.equal(sameTerms.emptyStatus, 'review_required');
  assert(sameTerms.emptyText.includes('Enter proposed wording'));
  checks.push('revision changes and identical terms both require meaning review');

  const evaluation = {
    method: 'doxa-reading-ui-engineering-v1', origin: 'fictional_authored',
    annotationProvenance: 'ai_provisional', independentHumanReviewCount: 0,
    browser: 'Chromium', passed: checks.length, failed: 0, checks,
    scope: 'Browser behavior, source fidelity and bounded change prompts. These fixtures supply no independent estimate of semantic accuracy.',
  };
  await writeFile(new URL('./browser-evaluation.json', import.meta.url), JSON.stringify(evaluation, null, 2) + '\n');
  console.log(JSON.stringify(evaluation));
} finally {
  await browser?.close();
  server?.kill();
  await rm(temporary, { recursive: true, force: true });
}
