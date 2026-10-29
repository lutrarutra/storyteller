#!/usr/bin/env node
// Live updates: a report served by `storyteller serve` changes while it is open.
// The page must patch itself without reloading: the changed section updates, an
// unchanged section keeps its explorer settings, and a new section appears in place.
//
//   node tests/e2e/check_live.mjs PYTHON WORK_DIR
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright-core';

const [python, workArg] = process.argv.slice(2);
const work = resolve(workArg || 'e2e-live');
mkdirSync(work, { recursive: true });
const failures = [];
const check = (condition, message) => {
  console.log(`${condition ? 'ok  ' : 'FAIL'} ${message}`);
  if (!condition) failures.push(message);
};

const run = (code) => {
  const result = spawnSync(python, ['-c', code], { cwd: work, encoding: 'utf8' });
  if (result.status !== 0) throw new Error(result.stderr);
};

run(`
import storyteller as sty
from storyteller.demo import simulated_cells
r = sty.Report.open("reports/live", title="Live report")
r.pending("later", "Later", note="not yet")
with r.section("qc", "QC"):
    r.explorer(simulated_cells(500), [sty.Threshold("n_genes", min=500)], [sty.Stat("{n_pass} pass"), sty.Histogram("n_genes")], static=False)
with r.section("notes", "Notes"):
    r.text("version one")
`);

const port = 8800 + Math.floor(Math.random() * 100);
const server = spawn(python, ['-m', 'storyteller.cli', 'serve', 'reports/live', '--port', String(port)], { cwd: work });
await new Promise((ready) => server.stdout.once('data', ready));

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.waitForFunction(() => document.querySelector('[data-cf-explorer]')?.classList.contains('cf-ready'));
  await page.evaluate(() => (window.__marker = 'same page'));
  const input = page.locator('input[type=number][data-cf-bound=min]');
  await input.fill('1500');
  await input.dispatchEvent('input');
  await page.waitForTimeout(200);

  run(`
import storyteller as sty
r = sty.Report.open("reports/live")
with r.section("notes", "Notes"):
    r.text("version two")
with r.section("later", "Later"):
    r.text("filled in")
`);
  await page.waitForFunction(() => document.querySelector('#s-notes')?.textContent.includes('version two'), null, { timeout: 10000 });
  check(true, 'changed section updated in the open page');
  check((await page.evaluate(() => window.__marker)) === 'same page', 'page patched in place, not reloaded');
  check((await input.inputValue()) === '1500', 'unchanged section kept its explorer setting');
  check(await page.locator('#s-later').textContent().then((t) => t.includes('filled in')), 'planned section filled in');
  const order = await page.evaluate(() => [...document.querySelectorAll('[data-cf-slot] > section')].map((s) => s.id));
  check(JSON.stringify(order) === JSON.stringify(['s-later', 's-qc', 's-notes']), `section order kept: ${order.join(', ')}`);
  check(errors.length === 0, `no page errors ${errors.join(' | ')}`);
} finally {
  await browser.close();
  server.kill();
}
if (failures.length) process.exit(1);
