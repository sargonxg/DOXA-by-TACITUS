import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../web/node_modules/playwright/index.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const server = spawn(process.execPath, [resolve(root, 'web/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '8167', '--strictPort'], {
  cwd: resolve(root, 'web'), stdio: ['ignore', 'pipe', 'pipe'],
});
let output = '';
const ready = new Promise((resolveReady, reject) => {
  const timer = setTimeout(() => reject(new Error('Project test server did not start. ' + output)), 20_000);
  const inspect = chunk => {
    output += chunk.toString();
    if (output.includes('Local:')) { clearTimeout(timer); resolveReady(); }
  };
  server.stdout.on('data', inspect);
  server.stderr.on('data', inspect);
  server.once('exit', code => { clearTimeout(timer); reject(new Error('Project test server exited ' + code + '. ' + output)); });
});
let browser;
const checks = [];
try {
  await ready;
  browser = await chromium.launch({ executablePath: '/usr/bin/chromium' });
  const context = await browser.newContext();
  const requests = [];
  context.on('request', request => requests.push({ url: request.url(), method: request.method(), body: request.postData() }));
  await context.route('**/project-test', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><title>Project checks</title></head><body>Local project checks</body></html>' }));
  const page = await context.newPage();
  await page.goto('http://127.0.0.1:8167/project-test');
  const initial = await page.evaluate(async () => {
    const { saveProject } = await import('/src/project-store.ts');
    const passage = 'PROJECT-PRIVATE-SENTINEL The minister called the policy a reckless failure. \u{1f4d6}';
    const methods = { text: 'We compared policy statements from a stratified sample of parliamentary debates.', section: 'methods', index: 2, firstLine: 2, lastLine: 2 };
    const results = { text: 'The sampled policy statements contained attributed criticism in both comparison groups.', section: 'results', index: 3, firstLine: 3, lastLine: 3 };
    const limitations = { text: 'The sample excludes local councils and cannot establish the effect of the policy.', section: 'limitations', index: 4, firstLine: 4, lastLine: 4 };
    const source = [passage, methods.text, results.text, limitations.text].join('\n');
    const file = new File([source], 'Review source.txt', { type: 'text/plain', lastModified: 1700000000000 });
    const finding = { category: 'model', check: 'evaluative_framing', text: passage, section: 'opening', passage: 1, firstLine: 1, lastLine: 1 };
    const report = {
      method: 'doxa-document-audit-v1', modelRevision: 'fixture-revision', mode: 'academic', source: 'text', complete: true,
      progress: { passages: 4, screened: 4, pages: 0, totalPages: null, emptyPages: 0, extractionErrors: 0, bytes: file.size, totalBytes: file.size },
      sections: { opening: { eligible: 1, screened: 1, flagged: 1, reference: 0, short: 0 } },
      criteria: { evaluative_framing: { present: 1, absent: 0, uncertain: 0, abstained: 0, unsupported: 0 } },
      sectionCriteria: { opening: { evaluative_framing: { present: 1, absent: 0, uncertain: 0, abstained: 0, unsupported: 0 } } },
      cues: {}, research: { bodyPassages: 1, causalWithCitationMarker: 0, causalWithoutCitationMarker: 0 },
      claimReview: { method: 'causal-sentence-leads-v1', counts: {} }, retainedFindings: [finding], supportPassages: [methods, results], limitationPassages: [limitations], supportLimitReached: false,
      sampling: { maximumPerCheck: 12, maximumPerModelCheck: 240, seenByCheck: { 'model:evaluative_framing': 1 } },
      futureQuality: [{ page: 1, status: 'text', positionedLines: [{ text: source, x: 20, y: 30 }] }], processingMs: 1450.5,
    };
    const decision = { document: 0, finding, disposition: 'needs_context', note: 'Read the source before judging.', recordedAt: new Date().toISOString(),
      speaker: 'minister', evidenceSpans: [{ start: 0, end: 7 }], annotations: { provenance: 'reviewer_entered', speaker: 'minister', target: 'policy', scopeNote: 'Attribute the judgment to its speaker.', evidence: [
        { role: 'supporting', start: passage.indexOf('reckless'), end: passage.indexOf('reckless') + 'reckless failure'.length, text: 'reckless failure' },
      ] }, mitigation: { status: 'reviewer_draft', proposedText: 'The minister criticized the policy.', purpose: 'Attribute the judgment.',
        checks: { scope: true, position: true, evidence: false }, computedMitigationChecks: [{ kind: 'attribution', status: 'review' }] } };
    const claim = 'The policy caused political disagreement.';
    const evidence = { method: 'doxa-claim-selector-v1', claim, verdict: 'insufficient_context', scores: [0.1, 0.2, 0.7], leads: [], passages: 3, shortlisted: 2, screened: 2, unreadablePages: 0, emptyPages: 0,
      studyPacket: { method: 'doxa-study-context-v1', claim, sections: [methods, results, limitations].map(window => ({ section: window.section, window: { ...window, relevance: 0.5 }, selection: 'lexical_candidate' })), unavailable: [], noMatchingPassage: [] } };
    const snapshot = { documents: [{ file, author: 'Supplied label', report, sample: false, metadata: { event: 'event-one', topic: 'transport', genre: 'statement', period: '2024' } }],
      selectedDocument: 0, decisions: [['decision-one', decision]], evidenceChecks: [['check-one', { document: 0, result: evidence }]], failures: [], canceled: false,
      extra: { reviewTimings: { elapsedMs: 1450 }, contextWordings: [['decision-one', { verdict: 'review', score: 0.42 }]], purpose: 'Research review' } };
    const saved = await saveProject(snapshot, { name: 'Local review fixture' });
    return { id: saved.id, revision: saved.revision, sha256: saved.snapshot.documents[0].sha256, source };
  });
  assert.equal(initial.revision, 1);
  assert.match(initial.sha256, /^[a-f0-9]{64}$/);
  checks.push('Saved original files, metadata, reports, review decisions, drafts and future JSON fields');
  await page.reload();
  const reopened = await page.evaluate(async ({ id }) => {
    const { loadProject, listProjects } = await import('/src/project-store.ts');
    const record = await loadProject(id);
    return { name: record.name, revision: record.revision, text: await record.snapshot.documents[0].file.text(),
      metadata: record.snapshot.documents[0].metadata, quality: record.snapshot.documents[0].report.futureQuality,
      draft: record.snapshot.decisions[0][1].mitigation, speaker: record.snapshot.decisions[0][1].speaker,
      annotations: record.snapshot.decisions[0][1].annotations, packet: record.snapshot.evidenceChecks[0][1].result.studyPacket,
      extra: record.snapshot.extra, summaries: await listProjects() };
  }, { id: initial.id });
  assert.equal(reopened.text, initial.source);
  assert.equal(reopened.metadata.event, 'event-one');
  assert.equal(reopened.quality[0].positionedLines[0].text, initial.source);
  assert.equal(reopened.draft.computedMitigationChecks[0].kind, 'attribution');
  assert.equal(reopened.speaker, 'minister');
  assert.equal(reopened.annotations.evidence[0].text, 'reckless failure');
  assert.equal(reopened.packet.sections.length, 3);
  assert.equal(reopened.extra.reviewTimings.elapsedMs, 1450);
  assert.equal(reopened.summaries[0].documentCount, 1);
  checks.push('Reopened after a page reload with exact original text and unknown JSON extensions');

  const second = await context.newPage();
  await second.goto('http://127.0.0.1:8167/project-test');
  for (const tab of [page, second]) await tab.evaluate(async ({ id }) => {
    const { loadProject } = await import('/src/project-store.ts');
    window.projectSnapshot = (await loadProject(id)).snapshot;
  }, { id: initial.id });
  const concurrent = await Promise.all([page, second].map((tab, index) => tab.evaluate(async ({ id, index }) => {
    const { saveProject } = await import('/src/project-store.ts');
    try {
      const value = await saveProject(window.projectSnapshot, { id, name: 'Tab ' + index, expectedRevision: 1 });
      return { status: 'fulfilled', revision: value.revision };
    } catch (error) { return { status: 'rejected', code: error.code }; }
  }, { id: initial.id, index })));
  assert.equal(concurrent.filter(item => item.status === 'fulfilled' && item.revision === 2).length, 1);
  assert.equal(concurrent.filter(item => item.status === 'rejected' && item.code === 'conflict').length, 1);
  checks.push('Concurrent saves preserve one revision and reject the stale writer');

  const archive = await page.evaluate(async ({ id }) => {
    const { loadProject, listProjects } = await import('/src/project-store.ts');
    const { exportProject, importProject } = await import('/src/project-format.ts');
    const record = await loadProject(id);
    const blob = await exportProject(record);
    const recovered = await importProject(blob);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    bytes[bytes.length - 1] ^= 1;
    let tampered;
    try { await importProject(new Blob([bytes])); } catch (error) { tampered = error.code; }
    let truncated;
    try { await importProject(blob.slice(0, blob.size - 1)); } catch (error) { truncated = error.code; }
    let trailing;
    try { await importProject(new Blob([blob, 'x'])); } catch (error) { trailing = error.code; }
    const metadata = new Uint8Array(await blob.arrayBuffer());
    metadata[new TextEncoder().encode('DOXA-PROJECT\n').length + 4 + 32] ^= 1;
    let metadataTampered;
    try { await importProject(new Blob([metadata])); } catch (error) { metadataTampered = error.code; }
    const controller = new AbortController();
    controller.abort();
    let aborted;
    try { await exportProject(record, { signal: controller.signal }); } catch (error) { aborted = error.code; }
    return { source: await recovered.snapshot.documents[0].file.text(), sha256: recovered.snapshot.documents[0].sha256,
      draft: recovered.snapshot.decisions[0][1].mitigation.proposedText, extra: recovered.snapshot.extra,
      type: recovered.snapshot.documents[0].file.type, tampered, truncated, trailing, metadataTampered, aborted, projects: (await listProjects()).length };
  }, { id: initial.id });
  assert.equal(archive.source, initial.source);
  assert.equal(archive.sha256, initial.sha256);
  assert.equal(archive.draft, 'The minister criticized the policy.');
  assert.equal(archive.extra.contextWordings[0][1].score, 0.42);
  assert.equal(archive.type, 'text/plain');
  assert.equal(archive.tampered, 'invalid');
  assert.equal(archive.truncated, 'invalid');
  assert.equal(archive.trailing, 'invalid');
  assert.equal(archive.metadataTampered, 'invalid');
  assert.equal(archive.aborted, 'aborted');
  assert.equal(archive.projects, 1);
  checks.push('Portable round trip preserves all data; tampered, truncated and appended bytes fail without database writes');

  const failures = await page.evaluate(async ({ id }) => {
    const { loadProject, saveProject, deleteProject } = await import('/src/project-store.ts');
    const record = await loadProject(id);
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (...args) {
      if (this.name === 'summaries') throw new DOMException('Quota test', 'QuotaExceededError');
      return Reflect.apply(put, this, args);
    };
    let quota;
    try { await saveProject(record.snapshot, { id, name: 'Quota failed', expectedRevision: 2 }); } catch (error) { quota = error.code; }
    finally { IDBObjectStore.prototype.put = put; }
    const preserved = await loadProject(id);
    const retried = await saveProject(preserved.snapshot, { id, name: 'Retried after quota', expectedRevision: 2 });
    let staleDelete;
    try { await deleteProject(id, { expectedRevision: 2 }); } catch (error) { staleDelete = error.code; }
    const controller = new AbortController();
    controller.abort();
    let canceled;
    try { await saveProject(record.snapshot, { name: 'Canceled', signal: controller.signal }); } catch (error) { canceled = error.code; }
    return { quota, preservedRevision: preserved.revision, retriedRevision: retried.revision, staleDelete, canceled };
  }, { id: initial.id });
  assert.equal(failures.quota, 'quota');
  assert.equal(failures.preservedRevision, 2);
  assert.equal(failures.retriedRevision, 3);
  assert.equal(failures.staleDelete, 'conflict');
  assert.equal(failures.canceled, 'aborted');
  checks.push('Quota failure and cancellation keep the saved revision; retry succeeds and stale deletion fails');

  const validation = await page.evaluate(async ({ id }) => {
    const { loadProject } = await import('/src/project-store.ts');
    const { prepareProjectSnapshot, exportProject, importProject } = await import('/src/project-format.ts');
    const record = await loadProject(id);
    const note = record.snapshot.decisions[0][1].note;
    const copying = prepareProjectSnapshot(record.snapshot);
    record.snapshot.decisions[0][1].note = 'Changed while hashing';
    const isolated = await copying;
    const snapshotStable = isolated.decisions[0][1].note === note;
    record.snapshot.decisions[0][1].note = note;
    const old = structuredClone(record.snapshot);
    delete old.documents[0].report.sectionCriteria;
    delete old.documents[0].report.supportPassages;
    delete old.documents[0].report.supportLimitReached;
    delete old.documents[0].report.sampling.maximumPerModelCheck;
    old.evidenceChecks = [];
    const migrated = await prepareProjectSnapshot(old);
    const html = structuredClone(record.snapshot);
    const htmlFile = new File(['<html><script>throw new Error("active")</script></html>'], 'pretend.pdf', { type: 'text/html' });
    html.documents[0].file = htmlFile;
    delete html.documents[0].sha256;
    let htmlRejected;
    try { await prepareProjectSnapshot(html); } catch (error) { htmlRejected = error.code; }
    const pdfBytes = '%PDF-1.4\nfixture';
    const pdfFile = new File([pdfBytes], 'fixture.pdf', { type: 'text/html' });
    const pdf = structuredClone(record.snapshot);
    pdf.documents[0].file = pdfFile;
    delete pdf.documents[0].sha256;
    pdf.documents[0].report.source = 'pdf';
    Object.assign(pdf.documents[0].report.progress, { pages: 1, totalPages: 1, bytes: pdfFile.size, totalBytes: pdfFile.size });
    pdf.documents[0].report.sourceQuality = [{ page: 1, width: 612, height: 792, source: 'text_layer', nativeCharacters: 1200, characters: 1170, columns: 2,
      readingOrder: 'column_heuristic', tableRows: 0, smallTextLines: 1, imageOperators: 0, geometryScope: 'retained_findings', originalLineCount: 4,
      lines: [{ line: 1, box: [-2.5, 40, 525.2, 12], fragments: [0, 1], source: 'text_layer' }, { line: 4, box: [30, 120, 500, 12], fragments: [8], source: 'ocr', confidence: 92.5 }],
      excludedMargins: [{ line: 3, text: 'Repeated running title', box: [20, 0, 200, 10], reason: 'repeated_margin' }], issues: ['small_text_or_footnotes'], ocrConfidence: 92.5 }];
    const canonical = await prepareProjectSnapshot(pdf);
    const url = structuredClone(record.snapshot);
    url.documents[0].researchSource = { documentId: 'source', passageId: 'p1', title: 'Title', sourceUrl: 'javascript:alert(1)', sourceXPath: '/p[1]', paragraphIndex: 1,
      textSha256: record.snapshot.documents[0].sha256, license: { identifier: 'CC-BY-4.0', url: null }, topicCandidates: ['framing'] };
    let unsafeUrl;
    try { await prepareProjectSnapshot(url); } catch (error) { unsafeUrl = error.code; }
    return { defaults: migrated.documents[0].report.sectionCriteria, support: migrated.documents[0].report.supportPassages,
      cap: migrated.documents[0].report.sampling.maximumPerModelCheck, htmlRejected, canonicalType: canonical.documents[0].file.type,
      geometry: canonical.documents[0].report.sourceQuality[0], unsafeUrl, snapshotStable };
  }, { id: initial.id });
  assert.deepEqual(validation.defaults, {});
  assert.deepEqual(validation.support, []);
  assert.equal(validation.cap, 12);
  assert.equal(validation.htmlRejected, 'invalid');
  assert.equal(validation.canonicalType, 'application/pdf');
  assert.equal(validation.geometry.lines[1].line, 4);
  assert.equal(validation.geometry.lines[1].confidence, 92.5);
  assert.equal(validation.unsafeUrl, 'invalid');
  assert.equal(validation.snapshotStable, true);
  checks.push('Legacy optional fields migrate; active HTML masquerading as a PDF and unsafe source URLs are rejected');

  const extensions = await page.evaluate(async ({ id }) => {
    const { loadProject, listProjects } = await import('/src/project-store.ts');
    const { prepareProjectSnapshot, exportProject, importProject } = await import('/src/project-format.ts');
    const saved = await loadProject(id);
    const snapshot = structuredClone(saved.snapshot);
    const file = new File(['%PDF-1.4\nfixture'], 'quality.pdf', { type: 'application/pdf' });
    const document = snapshot.documents[0];
    document.file = file;
    delete document.sha256;
    document.report.source = 'pdf';
    Object.assign(document.report.progress, { pages: 1, totalPages: 1, bytes: file.size, totalBytes: file.size });
    document.report.sourceQuality = [{ page: 1, width: 612, height: 792, source: 'text_layer', nativeCharacters: 1200, characters: 1170, columns: 2,
      readingOrder: 'column_heuristic', tableRows: 0, smallTextLines: 1, imageOperators: 0, geometryScope: 'retained_findings', originalLineCount: 4,
      lines: [{ line: 1, box: [-2.5, 40, 525.2, 12], fragments: [0, 1], source: 'text_layer' }],
      excludedMargins: [{ line: 3, text: 'Repeated title', box: [20, 0, 200, 10], reason: 'repeated_margin' }], issues: ['small_text_or_footnotes'] }];
    const extended = { ...saved, snapshot: await prepareProjectSnapshot(snapshot) };
    const archive = await exportProject(extended);
    const magicLength = new TextEncoder().encode('DOXA-PROJECT\n').length;
    const headerLength = magicLength + 4 + 32;
    const bytes = new Uint8Array(await archive.arrayBuffer());
    const oldLength = new DataView(bytes.buffer).getUint32(magicLength, false);
    const original = JSON.parse(new TextDecoder().decode(bytes.slice(headerLength, headerLength + oldLength)));
    const tails = bytes.slice(headerLength + oldLength);
    async function forged(change) {
      const manifest = structuredClone(original);
      change(manifest.project.snapshot);
      const manifestBytes = new TextEncoder().encode(JSON.stringify(manifest));
      const header = bytes.slice(0, headerLength);
      new DataView(header.buffer).setUint32(magicLength, manifestBytes.length, false);
      header.set(new Uint8Array(await crypto.subtle.digest('SHA-256', manifestBytes)), magicLength + 4);
      return new Blob([header, manifestBytes, tails]);
    }
    const quality = value => value.documents[0].report.sourceQuality[0];
    const packet = value => value.evidenceChecks[0][1].result.studyPacket;
    const annotations = value => value.decisions[0][1].annotations;
    const corruptions = [
      ['quality string', value => { value.documents[0].report.sourceQuality = 'x'; }],
      ['quality lines object', value => { quality(value).lines = {}; }],
      ['quality line beyond original', value => { quality(value).lines[0].line = 5; }],
      ['quality original count absent', value => { delete quality(value).originalLineCount; }],
      ['quality duplicate pages', value => { value.documents[0].report.sourceQuality.push(structuredClone(quality(value))); }],
      ['quality source enum', value => { quality(value).source = 'invented'; }],
      ['quality box wrong shape', value => { quality(value).lines[0].box = [1, 2]; }],
      ['quality box negative extent', value => { quality(value).lines[0].box[2] = -1; }],
      ['quality box numeric string', value => { quality(value).lines[0].box[0] = '2'; }],
      ['quality fragment negative', value => { quality(value).lines[0].fragments = [-1]; }],
      ['quality line source enum', value => { quality(value).lines[0].source = 'html'; }],
      ['quality confidence outside percentage', value => { quality(value).ocrConfidence = 101; }],
      ['quality counter negative', value => { quality(value).characters = -1; }],
      ['quality issues object', value => { quality(value).issues = {}; }],
      ['quality excluded margin out of bounds', value => { quality(value).excludedMargins[0].line = 5; }],
      ['processing time negative', value => { value.documents[0].report.processingMs = -1; }],
      ['limitation pool object', value => { value.documents[0].report.limitationPassages = {}; }],
      ['limitation range reversed', value => { value.documents[0].report.limitationPassages[0].lastLine = 1; }],
      ['limitation section mismatch', value => { value.documents[0].report.limitationPassages[0].section = 'methods'; }],
      ['study sections object', value => { packet(value).sections = {}; }],
      ['study claim mismatch', value => { packet(value).claim = 'A different claim'; }],
      ['study method provenance', value => { packet(value).method = 'reviewer_verified'; }],
      ['study absent statuses', value => { packet(value).sections.pop(); }],
      ['study repeated statuses', value => { packet(value).unavailable.push('methods'); }],
      ['study unknown section', value => { packet(value).sections[0].section = 'conclusion'; }],
      ['study non-array no-match', value => { packet(value).noMatchingPassage = {}; }],
      ['study fabricated passage', value => { packet(value).sections[0].window.text = 'fabricated'; }],
      ['study changed passage range', value => { packet(value).sections[0].window.firstLine = 1; }],
      ['study selection provenance', value => { packet(value).sections[0].selection = 'verified_evidence'; }],
      ['annotation provenance', value => { annotations(value).provenance = 'model_inferred'; }],
      ['annotation wrong string type', value => { annotations(value).speaker = {}; }],
      ['annotation control character', value => { annotations(value).scopeNote = 'scope\u0000'; }],
      ['annotation oversized speaker', value => { annotations(value).speaker = 'x'.repeat(301); }],
      ['annotation evidence object', value => { annotations(value).evidence = {}; }],
      ['annotation fabricated text', value => { annotations(value).evidence[0].text = 'fabricated'; }],
      ['annotation selection beyond source', value => { annotations(value).evidence[0].end = 1_000_000; }],
      ['annotation invalid role', value => { annotations(value).evidence[0].role = 'verified'; }],
      ['annotation duplicate selection', value => { annotations(value).evidence.push(structuredClone(annotations(value).evidence[0])); }],
      ['annotation surrogate split', value => {
        const text = value.decisions[0][1].finding.text;
        const start = text.indexOf('\u{1f4d6}') + 1;
        annotations(value).evidence = [{ role: 'supporting', start, end: start + 1, text: text.slice(start, start + 1) }];
      }],
    ];
    const rejected = [];
    for (const [name, change] of corruptions) {
      let code;
      try { await importProject(await forged(change)); } catch (error) { code = error.code; }
      if (code !== 'invalid') throw new Error(name + ' was adopted: ' + String(code));
      rejected.push(name);
    }
    const alternative = await importProject(await forged(value => {
      packet(value).sections = packet(value).sections.slice(0, 1);
      packet(value).unavailable = ['results'];
      packet(value).noMatchingPassage = ['limitations'];
    }));
    const nonfinite = structuredClone(extended.snapshot);
    nonfinite.documents[0].report.sourceQuality[0].lines[0].box[0] = Number.POSITIVE_INFINITY;
    let finiteRejected;
    try { await prepareProjectSnapshot(nonfinite); } catch (error) { finiteRejected = error.code; }
    return { rejected, finiteRejected, alternative: alternative.snapshot.evidenceChecks[0][1].result.studyPacket, projects: (await listProjects()).length };
  }, { id: initial.id });
  assert.equal(extensions.rejected.length, 39);
  assert.equal(extensions.finiteRejected, 'invalid');
  assert.deepEqual(extensions.alternative.unavailable, ['results']);
  assert.deepEqual(extensions.alternative.noMatchingPassage, ['limitations']);
  assert.equal(extensions.projects, 1);
  checks.push('Rehashed crafted archives reject 39 malformed geometry, study-context and annotation extensions before adoption; source slices and finite bounds hold');
  assert(requests.every(request => request.method === 'GET' && request.body === null));
  assert(!JSON.stringify(requests).includes('PROJECT-PRIVATE-SENTINEL'));
  checks.push('Storage and portable operations make no source-content network requests');
  await page.evaluate(async ({ id }) => {
    const { deleteProject, listProjects } = await import('/src/project-store.ts');
    await deleteProject(id, { expectedRevision: 3 });
    if ((await listProjects()).length) throw new Error('Deleted project remains in summaries');
  }, { id: initial.id });
} finally {
  await browser?.close();
  server.kill();
}
const receipt = { passed: true, checks, note: 'Real Chromium IndexedDB and portable binary archive tests. The fixture is fictional.' };
await mkdir(resolve(root, 'improvement_release/projects'), { recursive: true });
await writeFile(resolve(root, 'improvement_release/projects/checks.json'), JSON.stringify(receipt, null, 2) + '\n');
console.log(JSON.stringify(receipt, null, 2));
