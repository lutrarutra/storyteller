#!/usr/bin/env node
// Opens a report in headless Chromium with the network blocked and checks that it
// works on its own: no requests leave the file, no console errors, explorers draw
// and react to a threshold change, the script-free view shows the static snapshots,
// and it prints to PDF. Screenshots and the PDF go to the output directory for a
// look by eye.
//
//   node tests/e2e/check_report.mjs REPORT.html [OUT_DIR]
//
// Chromium: playwright-core's own download (npx playwright-core install chromium),
// or CHROMIUM_PATH pointing at an installed Chrome/Chromium.
import { mkdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const [reportArg, outArg] = process.argv.slice(2);
if (!reportArg) {
  console.error('usage: check_report.mjs REPORT.html [OUT_DIR]');
  process.exit(2);
}
const url = pathToFileURL(resolve(reportArg)).href;
const out = resolve(outArg || 'e2e-output');
await mkdir(out, { recursive: true });

const failures = [];
const fail = (message) => {
  failures.push(message);
  console.error(`FAIL ${message}`);
};
const ok = (message) => console.log(`ok   ${message}`);

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
try {
  // -- with scripts ---------------------------------------------------------------------
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const outside = [];
  await context.route('**/*', (route) => {
    const target = route.request().url();
    if (target.startsWith('file:') || target.startsWith('data:') || target.startsWith('blob:')) return route.continue();
    outside.push(target);
    return route.abort();
  });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));
  page.on('pageerror', (error) => errors.push(String(error)));
  await page.goto(url, { waitUntil: 'load' });
  const explorers = await page.locator('[data-cf-explorer]').count();
  if (explorers) {
    await page.waitForFunction(
      () => [...document.querySelectorAll('[data-cf-explorer]')].every((e) => e.classList.contains('cf-ready')),
      null,
      { timeout: 20000 }
    );
    ok(`${explorers} explorer(s) decoded their data and drew`);
  }
  const plots = await page.locator('.js-plotly-plot').count();
  ok(`${plots} plotly chart(s) on the page`);
  await page.screenshot({ path: `${out}/screen-1440.png`, fullPage: true });

  if (explorers) {
    const stat = page.locator('[data-cf-explorer] .cf-live .cf-stat').first();
    const before = (await stat.count()) ? await stat.textContent() : null;
    const input = page.locator('[data-cf-explorer] input[type=number][data-cf-bound]').first();
    const value = Number(await input.inputValue());
    await input.fill(String(value * 3 || 1));
    await input.dispatchEvent('input');
    await page.waitForTimeout(250);
    const after = before !== null ? await stat.textContent() : null;
    if (before !== null && before === after) fail(`stat text did not change after moving a threshold (${before})`);
    else ok(`moving a threshold updates the numbers: "${before}" → "${after}"`);
    const settings = await page.evaluate(() => window.cfReport.settings());
    ok(`settings readable for the agent: ${JSON.stringify(settings[0])}`);
    await page.locator('[data-cf-explorer]').first().screenshot({ path: `${out}/explorer.png` });
  }

  // A cross-reference into a closed tab opens the tab and scrolls to the block.
  const intoTab = await page.evaluate(() =>
    [...document.querySelectorAll('a.cf-ref[href^="#"]')].findIndex((a) => {
      const target = document.getElementById(a.getAttribute('href').slice(1));
      return target && target.closest('.cf-tab-panel') && !target.offsetParent;
    })
  );
  if (intoTab >= 0) {
    await page.locator('a.cf-ref[href^="#"]').nth(intoTab).click();
    await page.waitForTimeout(200);
    const shown = await page.evaluate((index) => {
      const link = document.querySelectorAll('a.cf-ref[href^="#"]')[index];
      const target = document.getElementById(link.getAttribute('href').slice(1));
      const box = target.getBoundingClientRect();
      return target.offsetParent !== null && box.top >= -2 && box.top < window.innerHeight;
    }, intoTab);
    if (!shown) fail('a reference into a closed tab did not open the tab and scroll to the block');
    else ok('a reference into a closed tab opens the tab and scrolls to the block');
  }
  const brokenRefs = await page.locator('[data-cf-missing-ref]').count();
  if (brokenRefs) fail(`${brokenRefs} cross-reference(s) lead nowhere`);
  else ok(`${await page.locator('a.cf-ref').count()} cross-reference(s) resolve`);

  await page.setViewportSize({ width: 2560, height: 1300 });
  await page.waitForTimeout(300);
  const layout = await page.evaluate(() => {
    const sheet = document.querySelector('.cf-sheet').getBoundingClientRect();
    return { left: sheet.left, right: window.innerWidth - sheet.right, width: sheet.width };
  });
  if (layout.width > 1000 || Math.abs(layout.left - layout.right) > 40) fail(`wide screen: sheet not a centred column ${JSON.stringify(layout)}`);
  else ok(`wide screen keeps margins: sheet ${Math.round(layout.width)}px, ${Math.round(layout.left)}px either side`);
  await page.screenshot({ path: `${out}/screen-2560.png` });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 1) fail(`phone width scrolls sideways by ${overflow}px`);
  else ok('phone width: no sideways scroll');
  await page.screenshot({ path: `${out}/screen-390.png`, fullPage: true });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${out}/screen-dark.png` });
  await page.emulateMedia({ colorScheme: 'light' });

  // Chrome prints at the paper's width: A4 is 794 CSS pixels wide.
  await page.setViewportSize({ width: 794, height: 1123 });
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
  await page.emulateMedia({ media: 'print' });
  await page.waitForTimeout(400);
  await page.pdf({ path: `${out}/report.pdf`, printBackground: true, preferCSSPageSize: true });
  if (await page.locator('a.cf-ref[href^="#"]').count()) {
    // Chromium writes in-page links as named destinations; without them references are dead in the PDF.
    const pdf = (await readFile(`${out}/report.pdf`)).toString('latin1');
    if (!/\/Dest\s*\/[sb]-/.test(pdf)) fail('the PDF has no in-document links');
    else ok('cross-references are links in the PDF');
  }
  const clipped = await page.evaluate(() => {
    const column = document.querySelector('.cf-sheet').getBoundingClientRect().right;
    return [...document.querySelectorAll('.js-plotly-plot .main-svg')].filter((svg) => svg.getBoundingClientRect().right > column + 2).length;
  });
  if (clipped) fail(`${clipped} chart(s) wider than the printed column`);
  else ok('printed charts fit the page column');
  await page.screenshot({ path: `${out}/print-a4.png`, fullPage: true });
  ok(`PDF written to ${out}/report.pdf`);

  if (outside.length) fail(`requests left the file: ${outside.slice(0, 5).join(', ')}`);
  else ok('no network requests');
  if (errors.length) fail(`console errors: ${errors.slice(0, 5).join(' | ')}`);
  else ok('no console errors');
  await context.close();

  // -- without scripts (like VS Code's sanitised preview) ------------------------------------
  const plain = await browser.newContext({ viewport: { width: 1200, height: 1000 }, javaScriptEnabled: false });
  const still = await plain.newPage();
  await still.goto(url, { waitUntil: 'load' });
  const hiddenStatic = await still.evaluate(
    () => [...document.querySelectorAll('.cf-static')].filter((e) => getComputedStyle(e).display === 'none').length
  );
  const shownLive = await still.evaluate(
    () => [...document.querySelectorAll('.cf-live')].filter((e) => getComputedStyle(e).display !== 'none').length
  );
  if (hiddenStatic || shownLive) fail(`without scripts: ${hiddenStatic} snapshot(s) hidden, ${shownLive} live part(s) shown`);
  else ok('without scripts the static snapshots show and the live parts stay hidden');
  await still.screenshot({ path: `${out}/no-js.png`, fullPage: true });
  await plain.close();
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(`${failures.length} check(s) failed`);
  process.exit(1);
}
console.log(`all checks passed; screenshots in ${out}`);
