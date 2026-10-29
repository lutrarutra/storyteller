#!/usr/bin/env node
// Rebuilds src/storyteller/assets/{vendor,fonts} from pinned npm tarballs.
// Each tarball is checked against its registry integrity hash before a file is
// taken from it, and VERSIONS.json records what was installed.
//
//   node scripts/vendor.mjs          download and write the files
//   node scripts/vendor.mjs --check  only verify the files on disk against VERSIONS.json
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'storyteller', 'assets');

const PACKAGES = [
  {
    name: 'plotly.js-cartesian-dist-min',
    version: '4.1.2',
    integrity: 'sha512-3UkyAT7/U8f3sOLavLnUjA6mqFmnqEe246GZhxT0J3TT9oZ0mh6SgwsL1yo/3/4xfmRSDFdGOxe+EsvT2XiE0g==',
    files: { 'plotly-cartesian.min.js': 'vendor/plotly-cartesian.min.js', LICENSE: 'vendor/plotly.LICENSE' }
  },
  {
    name: 'plotly.js-strict-dist-min',
    version: '4.1.2',
    integrity: 'sha512-hEKh0xY+8m0ekp5qWpHDbo9hMQbPPXuSOQhciviSpMGNQBUbguvgja1P1v9/407Gy5RIx6xl54BNlZrU0DdTzA==',
    files: { 'plotly-strict.min.js': 'vendor/plotly-strict.min.js' }
  },
  {
    name: 'd3',
    version: '7.9.0',
    integrity: 'sha512-e1U46jVP+w7Iut8Jt8ri1YsPOvFpg46k+K8TpCb0P+zjCkjkPnV7WzfDJzMHy1LnA+wj5pLT1wjO901gLXeEhA==',
    files: { 'dist/d3.min.js': 'vendor/d3.min.js', LICENSE: 'vendor/d3.LICENSE' }
  },
  {
    name: '@fontsource-variable/dm-sans',
    version: '5.3.0',
    integrity: 'sha512-BpUG5bqePiDFMBM4ZtLSlPdAIOM990uB1dlXzIf4aw21yQR7BmykedLXXXMqGWsR18aMLOsbWccwJNh3ztTQaA==',
    files: {
      'files/dm-sans-latin-wght-normal.woff2': 'fonts/dm-sans-latin-wght-normal.woff2',
      'files/dm-sans-latin-wght-italic.woff2': 'fonts/dm-sans-latin-wght-italic.woff2',
      LICENSE: 'fonts/dm-sans.LICENSE'
    }
  },
  {
    name: '@fontsource-variable/fredoka',
    version: '5.3.0',
    integrity: 'sha512-13w98iw2RcmYfZQRZPLSu+0YJK3HHm5A2XrpU26moFIAePmfiKfvkmEJUgkc8r5GNR+pbDijOueS1KdGAMAWmQ==',
    files: { 'files/fredoka-latin-wght-normal.woff2': 'fonts/fredoka-latin-wght-normal.woff2', LICENSE: 'fonts/fredoka.LICENSE' }
  }
];

// The entries of an uncompressed tar archive (ustar; npm tarballs use no extensions we need).
function* untar(buffer) {
  for (let offset = 0; offset + 512 <= buffer.length; ) {
    const header = buffer.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString('utf8').replace(/\0.*$/s, '');
    if (!name) return;
    const prefix = header.subarray(345, 500).toString('utf8').replace(/\0.*$/s, '');
    const size = parseInt(header.subarray(124, 136).toString('utf8').trim() || '0', 8);
    const body = buffer.subarray(offset + 512, offset + 512 + size);
    yield { path: prefix ? `${prefix}/${name}` : name, body };
    offset += 512 + Math.ceil(size / 512) * 512;
  }
}

const sha256 = (data) => createHash('sha256').update(data).digest('hex');

async function check() {
  const versions = JSON.parse(await readFile(join(root, 'vendor', 'VERSIONS.json'), 'utf8'));
  let ok = true;
  for (const pkg of versions.packages) {
    for (const [target, digest] of Object.entries(pkg.files)) {
      const actual = sha256(await readFile(join(root, target)));
      if (actual !== digest) {
        console.error(`${target}: sha256 ${actual}, expected ${digest}`);
        ok = false;
      }
    }
  }
  if (!ok) process.exit(1);
  console.log('vendored files match VERSIONS.json');
}

async function vendor() {
  const record = [];
  for (const pkg of PACKAGES) {
    const base = pkg.name.split('/').pop();
    const url = `https://registry.npmjs.org/${pkg.name}/-/${base}-${pkg.version}.tgz`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    const tarball = Buffer.from(await response.arrayBuffer());
    const [algorithm, expected] = pkg.integrity.split('-');
    const actual = createHash(algorithm).update(tarball).digest('base64');
    if (actual !== expected) throw new Error(`${pkg.name}@${pkg.version}: integrity mismatch`);
    const wanted = new Map(Object.entries(pkg.files).map(([source, target]) => [`package/${source}`, target]));
    const files = {};
    for (const entry of untar(gunzipSync(tarball))) {
      const target = wanted.get(entry.path);
      if (!target) continue;
      await mkdir(dirname(join(root, target)), { recursive: true });
      await writeFile(join(root, target), entry.body);
      files[target] = sha256(entry.body);
      wanted.delete(entry.path);
    }
    if (wanted.size) throw new Error(`${pkg.name}: missing ${[...wanted.keys()].join(', ')}`);
    record.push({ name: pkg.name, version: pkg.version, integrity: pkg.integrity, files });
    console.log(`${pkg.name}@${pkg.version}: ${Object.keys(files).length} files`);
  }
  await writeFile(join(root, 'vendor', 'VERSIONS.json'), `${JSON.stringify({ packages: record }, null, 2)}\n`);
}

await (process.argv.includes('--check') ? check() : vendor());
