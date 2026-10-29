#!/usr/bin/env node
// Readers' comments (comments.js) in headless Chromium, in the three ways a report is open:
// - as a file: right-click a selection, comment, edit, delete; the comments survive a
//   reload, go into a saved copy that still works, and the agent imports them;
// - in VS Code (a stub host): each change goes to cfHost.saveComments;
// - from `storyteller serve`: each change is saved into the report, and an answer from
//   the agent reaches the open page.
//
//   node tests/e2e/check_comments.mjs PYTHON WORK_DIR
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const [python, workArg] = process.argv.slice(2);
const work = resolve(workArg || 'e2e-comments');
mkdirSync(work, { recursive: true });
const failures = [];
const check = (condition, message) => {
  console.log(`${condition ? 'ok  ' : 'FAIL'} ${message}`);
  if (!condition) failures.push(message);
};

const run = (code) => {
  const result = spawnSync(python, ['-c', code], { cwd: work, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr);
  return result.stdout;
};
const cli = (...args) => {
  const result = spawnSync(python, ['-m', 'storyteller.cli', ...args], { cwd: work, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr);
  return result.stdout;
};

run(`
import storyteller as sty
from storyteller.demo import simulated_cells
r = sty.Report.open("reports/c", title="Comments")
r.summary("Of 7,000 barcodes, **6,000 cells** pass QC (genes ≥ 500, mitochondrial reads < 15 %).")
with r.section("qc", "QC"):
    r.explorer(simulated_cells(500), [sty.Threshold("n_genes", min=500)], [sty.Stat("{n_pass} pass"), sty.Histogram("n_genes")], static=False)
with r.section("methods", "Methods"):
    r.text("Cells were filtered, then doublets were removed with scDblFinder.")
`);
const reportPath = join(work, 'reports', 'c.html');

/** Selects "6,000 cells pass QC", across the end of a <strong>, and returns its middle. */
const selectInSummary = (page) =>
  page.evaluate(() => {
    const strong = document.querySelector('[data-cf-part^="summary:"] p strong');
    strong.scrollIntoView({ block: 'center' });
    const range = document.createRange();
    range.setStart(strong.firstChild, strong.firstChild.data.indexOf('6,000'));
    range.setEnd(strong.nextSibling, strong.nextSibling.data.indexOf('QC') + 2);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    const box = range.getBoundingClientRect();
    return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
  });

const selectInMethods = (page) =>
  page.evaluate(() => {
    const paragraph = [...document.querySelectorAll('[data-cf-part="section:methods"] p')].find((p) => p.textContent.includes('doublets'));
    paragraph.scrollIntoView({ block: 'center' });
    const text = paragraph.firstChild;
    const range = document.createRange();
    range.setStart(text, text.data.indexOf('doublets'));
    range.setEnd(text, text.data.indexOf('removed') + 7);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    const box = range.getBoundingClientRect();
    return { x: box.left + 5, y: box.top + box.height / 2 };
  });

async function comment(page, select, kind, body) {
  const at = await select(page);
  await page.mouse.click(at.x, at.y, { button: 'right' });
  await page.locator('.cf-comment-menu').waitFor({ state: 'visible', timeout: 2000 });
  await page.click(`.cf-comment-menu [data-do="${kind}"]`);
  await page.fill('.cf-comment-editor textarea', body);
  await page.click('.cf-comment-editor [data-do="save"]');
}

const list = (page) => page.evaluate(() => cfComments.list());
const marked = (page, id) =>
  page.evaluate((id) => [...document.querySelectorAll(`mark[data-cf-comment="${id}"]`)].map((m) => m.textContent).join(''), id);

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
let server;
try {
  // -- as a file ---------------------------------------------------------------------------
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1300, height: 900 } });
  // Downloads, not the save dialog, which a headless browser cannot answer.
  await context.addInitScript(() => delete window.showSaveFilePicker);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));
  await page.goto(pathToFileURL(reportPath).href);
  await page.waitForFunction(() => document.querySelector('[data-cf-explorer]')?.classList.contains('cf-ready'));
  check(await page.locator('.cf-comments-bar').isHidden(), 'no comments: no comment bar');

  await comment(page, selectInSummary, 'request', 'Is 500 genes too strict?');
  let [first] = await list(page);
  check(first?.kind === 'request' && first.anchor.part.startsWith('summary:'), 'right-click on a selection adds a request');
  check((await marked(page, first.id)) === '6,000 cells pass QC', 'the highlight covers the selection across <strong>');
  check((await page.locator('mark.cf-mark').count()) === 2, 'one mark per text node');

  await page.hover(`mark[data-cf-comment="${first.id}"] >> nth=0`);
  check((await page.textContent('.cf-comment-card .cf-comment-body')) === 'Is 500 genes too strict?', 'hovering shows the comment');
  await page.click('.cf-comment-card [data-do="edit"]');
  await page.fill('.cf-comment-editor textarea', 'Say "kept" instead of "pass".');
  await page.click('.cf-comment-editor [data-do="save"]');
  check((await list(page))[0].body.startsWith('Say "kept"'), 'editing changes the comment');

  await comment(page, selectInMethods, 'note', 'temporary');
  const second = (await list(page))[1];
  await page.hover(`mark[data-cf-comment="${second.id}"] >> nth=0`);
  await page.click('.cf-comment-card [data-do="delete"]');
  check((await list(page)).length === 1 && !(await page.locator(`mark[data-cf-comment="${second.id}"]`).count()), 'deleting removes comment and highlight');

  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('mark.cf-mark').length === 2);
  check((await page.textContent('.cf-comments-bar')).includes('Not in the file yet'), 'unsaved comments survive a reload');

  const [download] = await Promise.all([page.waitForEvent('download'), page.click('.cf-comments-bar [data-do="save-copy"]')]);
  const copyPath = join(work, 'copy.html');
  await download.saveAs(copyPath);
  const copyHtml = readFileSync(copyPath, 'utf8');
  check(!/class="[^"]*(js-plotly-plot|cf-mark|cf-comment-ui)/.test(copyHtml), 'the saved copy is the page as built, without drawn charts or highlights');
  check(!(await page.textContent('.cf-comments-bar')).includes('Not in the file yet'), 'saving a copy clears "not in the file yet"');

  const fresh = await browser.newContext({ viewport: { width: 1300, height: 900 } });
  const reopened = await fresh.newPage();
  reopened.on('pageerror', (error) => errors.push(String(error)));
  await reopened.goto(pathToFileURL(copyPath).href);
  await reopened.waitForFunction(() => document.querySelector('[data-cf-explorer]')?.classList.contains('cf-ready'));
  const again = await list(reopened);
  check(again.length === 1 && (await marked(reopened, again[0].id)) === '6,000 cells pass QC', 'the saved copy shows its comment');
  check(!(await reopened.textContent('.cf-comments-bar')).includes('Not in the file yet'), 'the saved copy has nothing unsaved');

  // A live update of that copy: rewording elsewhere keeps the highlight; removing its words does not.
  const rehash = (html, tag) => html.replace(/(data-cf-part="summary:[^"]*" data-cf-hash=")[^"]+/, `$1${tag}`);
  await reopened.evaluate((html) => cfReport.patch(html), rehash(copyHtml, 'r1').replace('Of 7,000 barcodes', 'Of all 7,000 barcodes'));
  check((await marked(reopened, again[0].id)) === '6,000 cells pass QC', 'the highlight comes back after a live update');
  await reopened.evaluate((html) => cfReport.patch(html), rehash(copyHtml, 'r2').replace("</strong> pass QC", "</strong> were kept"));
  check((await reopened.textContent('.cf-comments-bar')).includes('1 unanchored'), 'a comment whose words are gone is unanchored');
  await fresh.close();

  check(cli('comments', 'import', 'reports/c', copyPath).trim() === '1 added, 0 updated', 'the agent imports the comments from the copy');
  check(cli('comments', 'list', 'reports/c').includes('> Say "kept" instead of "pass".'), 'and lists them');
  check(errors.length === 0, `no page errors ${errors.join(' | ')}`);
  await context.close();

  // -- in VS Code ------------------------------------------------------------------------------
  const vscode = await browser.newContext({ viewport: { width: 1300, height: 900 } });
  await vscode.addInitScript(() => {
    window.cfHost = {
      send: (text) => (window.__sent = text),
      reload() {},
      saveComments: (json) => (window.__saved = json)
    };
  });
  const hosted = await vscode.newPage();
  await hosted.goto(pathToFileURL(reportPath).href);
  await hosted.waitForSelector('mark.cf-mark'); // the page now has the imported comment
  await comment(hosted, selectInMethods, 'request', 'Which version of scDblFinder?');
  const saved = JSON.parse(await hosted.evaluate(() => window.__saved));
  check(saved.length === 2 && saved[1].body === 'Which version of scDblFinder?', 'in VS Code each change goes to the host');
  check(!(await hosted.locator('[data-do="save-copy"]').count()), 'in VS Code there is no "Save copy"');
  await hosted.click('.cf-comments-bar [data-do="send"]');
  const sent = await hosted.evaluate(() => window.__sent);
  check(sent.includes(saved[1].id) && sent.includes('storyteller comments resolve'), '"Send to agent" puts the requests in the feedback box');
  await vscode.close();

  // -- from storyteller serve ---------------------------------------------------------------
  const port = 8900 + Math.floor(Math.random() * 100);
  server = spawn(python, ['-m', 'storyteller.cli', 'serve', 'reports/c', '--port', String(port)], { cwd: work });
  await new Promise((ready) => server.stdout.once('data', ready));
  const served = await browser.newPage({ viewport: { width: 1300, height: 900 } });
  await served.goto(`http://127.0.0.1:${port}/`);
  await served.waitForSelector('mark.cf-mark');
  await comment(served, selectInMethods, 'note', 'Cite the paper.');
  await served.waitForFunction(() => !document.querySelector('.cf-comments-bar').textContent.includes('Not saved'));
  await served.waitForTimeout(300);
  const stored = JSON.parse(readFileSync(join(work, 'reports', 'c.report', 'comments.json'), 'utf8'));
  check(stored.some((c) => c.body === 'Cite the paper.'), 'served: the comment is saved into the report');

  const id = stored.find((c) => c.kind === 'request').id;
  cli('comments', 'resolve', 'reports/c', id, 'Changed to kept.');
  await served.waitForFunction((id) => cfComments.list().find((c) => c.id === id)?.status === 'resolved', id, { timeout: 10000 });
  check(!(await served.locator(`mark[data-cf-comment="${id}"]`).count()), 'served: the agent resolves it and the open page updates');

  const forged = await fetch(`http://127.0.0.1:${port}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: 'https://example.com' },
    body: '[]'
  });
  check(forged.status === 403, 'served: other sites cannot write comments');
} finally {
  server?.kill();
  await browser.close();
}

console.log(failures.length ? `\n${failures.length} failed` : '\nall comment checks passed');
process.exit(failures.length ? 1 : 0);
