// The runtime's pure functions; the same rules are checked in Python by tests/test_report.py.
import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const runtime = require('../../src/storyteller/assets/runtime.js');

function columns(spec) {
  // Pack like pack.py: f32 / u8 / u16 codes, each aligned to 4 bytes.
  const parts = [];
  const descriptions = [];
  let offset = 0;
  for (const [name, kind, values, categories] of spec) {
    const typed = kind === 'f32' ? Float32Array.from(values) : kind === 'u8' ? Uint8Array.from(values) : Uint16Array.from(values);
    const bytes = new Uint8Array(typed.buffer);
    const pad = (4 - (offset % 4)) % 4;
    if (pad) parts.push(new Uint8Array(pad));
    offset += pad;
    descriptions.push({ name, kind, offset, length: values.length, categories });
    parts.push(bytes);
    offset += bytes.length;
  }
  const buffer = new Uint8Array(offset);
  let at = 0;
  for (const part of parts) {
    buffer.set(part, at);
    at += part.length;
  }
  return runtime.columnViews(buffer.buffer, descriptions);
}

const data = columns([
  ['genes', 'f32', [100, 600, 900, NaN, 1500]],
  ['mt', 'f32', [2, 30, 5, 1, 12]],
  ['sample', 'cat', [0, 1, 0, 65535, 1], ['A', 'B']],
  ['doublet', 'u8', [0, 0, 1, 0, 0]]
]);

const controls = [
  { kind: 'threshold', column: 'genes', min: 500 },
  { kind: 'threshold', column: 'mt', max: 15 },
  { kind: 'select', column: 'sample' },
  { kind: 'toggle', column: 'doublet', value: false }
];

test('thresholds set pass, select and toggle set the subset', () => {
  const { subset, pass } = runtime.computeMasks(data, controls, {}, 5);
  assert.deepEqual([...pass], [0, 0, 1, 0, 1]);
  assert.deepEqual([...subset], [1, 1, 1, 1, 1]);
  const chosen = runtime.computeMasks(data, controls, { sample: { value: 'B' }, doublet: { value: true } }, 5);
  assert.deepEqual([...chosen.subset], [0, 0, 0, 0, 0]);
  const onlyB = runtime.computeMasks(data, controls, { sample: { value: ['B'] } }, 5);
  assert.deepEqual([...onlyB.subset], [0, 1, 0, 0, 1]);
});

test('settings override control defaults', () => {
  const { pass } = runtime.computeMasks(data, controls, { genes: { min: 50 }, mt: { max: 40 } }, 5);
  assert.deepEqual([...pass], [1, 1, 1, 0, 1]);
  assert.deepEqual(runtime.settingsFor(controls, { genes: { min: 50 } }), {
    genes: { min: 50 },
    mt: { max: 15 },
    sample: { value: null },
    doublet: { value: false }
  });
});

test('stats and templates match the Python formatter', () => {
  const stats = runtime.computeStats(Uint8Array.from([1, 1, 1, 0]), Uint8Array.from([1, 0, 1, 1]));
  assert.deepEqual(stats, { n: 3, n_pass: 2, n_fail: 1, pct_pass: 2 / 3, pct_fail: 1 / 3 });
  const big = { n: 12345, n_pass: 6789, n_fail: 5556, pct_pass: 0.55, pct_fail: 0.45 };
  assert.equal(runtime.formatTemplate('{n_pass:,} of {n:,} pass ({pct_pass:.1%})', big), '6,789 of 12,345 pass (55.0%)');
  assert.equal(runtime.formatTemplate('{n} rows, {pct_fail:.2f}', big), '12345 rows, 0.45');
  assert.equal(runtime.formatTemplate('{unknown}', big), '{unknown}');
});

test('histogram bins: linear and log, last edge inclusive', () => {
  const values = Float32Array.from([0, 1, 2, 3, 4, NaN]);
  const edges = runtime.binEdges(values, null, 4, false);
  assert.deepEqual(edges, [0, 1, 2, 3, 4]);
  assert.deepEqual(runtime.binCounts(values, null, edges, false), [1, 1, 1, 2]);
  const logEdges = runtime.binEdges(Float32Array.from([1, 10, 100, 0]), null, 2, true);
  assert.deepEqual(logEdges, [0, 1, 2]);
  assert.deepEqual(runtime.binCounts(Float32Array.from([1, 10, 100, 0]), null, logEdges, true), [1, 2]);
});

test('masked values are left out of bins', () => {
  const values = Float32Array.from([1, 2, 3, 4]);
  const mask = Uint8Array.from([1, 0, 1, 0]);
  const edges = runtime.binEdges(values, mask, 2, false);
  assert.deepEqual(edges, [1, 2, 3]);
  assert.deepEqual(runtime.binCounts(values, mask, edges, false), [1, 1]);
});

test('sampling is stable as the mask changes', () => {
  const order = runtime.permutation(1000, 7);
  assert.equal(new Set(order).size, 1000);
  const all = runtime.sampleIndices(order, new Uint8Array(1000).fill(1), 50);
  const half = new Uint8Array(1000).map((_, i) => (i % 2 ? 1 : 0));
  const some = runtime.sampleIndices(order, half, 50);
  assert.equal(all.length, 50);
  assert.ok(some.every((i) => i % 2 === 1));
  // Points kept by both masks are the same points.
  const shared = all.filter((i) => i % 2 === 1);
  assert.ok(shared.every((i) => some.includes(i)));
});

test('merge is deep and lets later values win', () => {
  assert.deepEqual(runtime.merge({ a: { b: 1, c: 2 }, d: [1] }, { a: { c: 3 }, d: [2] }), { a: { b: 1, c: 3 }, d: [2] });
});

test('gzip data decodes into typed views', async () => {
  const floats = Float32Array.from([1.5, 2.5]);
  const compressed = gzipSync(Buffer.from(floats.buffer));
  const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
  const buffer = await new Response(stream).arrayBuffer();
  const views = runtime.columnViews(buffer, [{ name: 'x', kind: 'f32', offset: 0, length: 2 }]);
  assert.deepEqual([...views.x.values], [1.5, 2.5]);
});
