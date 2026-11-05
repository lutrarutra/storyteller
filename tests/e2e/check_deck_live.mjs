#!/usr/bin/env node
// Live updates of a deck: its viewer, served by `storyteller serve`, is open while a
// script re-runs one slide. The page must patch itself without reloading: that slide
// shows the new content at once (marked "updating"), then LibreOffice's new drawing,
// and the other slides stay as they were.
//
//   node tests/e2e/check_deck_live.mjs PYTHON WORK_DIR
// Needs LibreOffice (or STORYTELLER_SOFFICE) and python-pptx.
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { chromium } from 'playwright-core';

const [python, workArg] = process.argv.slice(2);
const work = resolve(workArg || 'e2e-deck-live');
mkdirSync(work, { recursive: true });
const failures = [];
const check = (condition, message) => {
  console.log(`${condition ? 'ok  ' : 'FAIL'} ${message}`);
  if (!condition) failures.push(message);
};

const run = (code) => {
  const result = spawnSync(python, ['-c', code], { cwd: work, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr);
  return result.stderr;
};

run(`
import storyteller as sty
d = sty.Deck.open("decks/live", title="Live deck")
with d.slide("intro", "Live deck", subtitle="Watching it change"):
    pass
with d.section("main", "Main part"):
    with d.slide("one", "The first point"):
        d.text("- alpha\\n- beta")
    with d.slide("two", "The second point"):
        d.metrics({"Cells": 1200, "Samples": 4})
`);

const port = 8790 + Math.floor(Math.random() * 200);
const server = spawn(python, ['-m', 'storyteller.cli', 'serve', 'decks/live', '--port', String(port)], {
  cwd: work,
  stdio: ['ignore', 'pipe', 'inherit']
});
await new Promise((done) => server.stdout.once('data', done));

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.goto(`http://127.0.0.1:${port}/`);
  check((await page.locator('.cf-slide').count()) === 4, 'four slides: title, section header and two content slides');
  check((await page.locator('.cf-slide-image').count()) === 4, 'every slide shows its drawing');
  await page.evaluate(() => {
    window.cfNotReloaded = true;
    document.querySelector('[data-cf-part="slide:two"]').dataset.kept = 'yes';
  });

  // Re-run one slide, without waiting for the drawing: the viewer shows the change first.
  const rerun = spawn(python, ['-c', `
import storyteller as sty
d = sty.Deck.open("decks/live")
with d.slide("one", "The first point, revised"):
    d.text("- alpha\\n- beta\\n- gamma")
`], { cwd: work, stdio: 'inherit' });
  const one = page.locator('[data-cf-part="slide:one"]');
  await page.waitForFunction(
    () => document.querySelector('[data-cf-part="slide:one"] .cf-slide-title')?.textContent === 'The first point, revised',
    null,
    { timeout: 15000 }
  );
  check(await one.evaluate((el) => el.classList.contains('cf-slide-stale')), 'the changed slide is marked as being redrawn');
  check((await one.locator('.cf-slide-badge').textContent()) === 'Updating…', 'it says "Updating…" over the old drawing');
  await new Promise((done) => rerun.once('exit', done));
  await page.waitForFunction(
    () => !document.querySelector('[data-cf-part="slide:one"]').classList.contains('cf-slide-stale'),
    null,
    { timeout: 30000 }
  );
  check(true, 'the new drawing replaces it');
  check(await page.evaluate(() => window.cfNotReloaded === true), 'the page patched itself instead of reloading');
  check(
    (await page.locator('[data-cf-part="slide:two"]').getAttribute('data-kept')) === 'yes',
    'the slide that did not change is the same element'
  );
  check((await page.locator('.cf-slide').count()) === 4, 'still four slides');

  // A new slide appears in its section, in place.
  run(`
import storyteller as sty
d = sty.Deck.open("decks/live")
with d.section("main", "Main part"):
    with d.slide("one", "The first point, revised"):
        d.text("- alpha\\n- beta\\n- gamma")
    with d.slide("between", "A point in between"):
        d.text("Added later")
    with d.slide("two", "The second point"):
        d.metrics({"Cells": 1200, "Samples": 4})
`);
  await page.waitForSelector('[data-cf-part="slide:between"] .cf-slide-image', { timeout: 30000 });
  const order = await page.locator('[data-cf-slot="section:main"] > .cf-slide').evaluateAll((els) => els.map((el) => el.dataset.cfPart));
  check(
    JSON.stringify(order) === JSON.stringify(['head:main', 'slide:one', 'slide:between', 'slide:two']),
    `the new slide sits between the others (${order.join(', ')})`
  );
  check(errors.length === 0, `no page errors${errors.length ? `: ${errors.join('; ')}` : ''}`);
} finally {
  await browser.close();
  server.kill();
}

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log('\nall deck live checks passed');
