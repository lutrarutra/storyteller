/* storyteller runtime: interactive charts, explorers, theme, print and live updates.
 *
 * Plain ES2020 without a build step. Everything works offline: the data and the
 * plotly bundle are embedded in the page. Without this script (a sanitised
 * preview) the report still reads: every interactive block has a static
 * snapshot, which this script swaps for the live version.
 *
 * The pure functions at the top are exported for node tests (tests/js).
 */
(function (global) {
  'use strict';

  // -- pure helpers ---------------------------------------------------------------

  const MISSING_CODE = 65535;

  /** Typed-array views of a decompressed column buffer. */
  function columnViews(buffer, columns) {
    const out = {};
    for (const column of columns) {
      let values;
      if (column.kind === 'f32') values = new Float32Array(buffer, column.offset, column.length);
      else if (column.kind === 'u8') values = new Uint8Array(buffer, column.offset, column.length);
      else values = new Uint16Array(buffer, column.offset, column.length);
      out[column.name] = { kind: column.kind, values, categories: column.categories || null };
    }
    return out;
  }

  /** subset (Select/Toggle) and pass (Threshold) masks as Uint8Arrays; mirrors explorer.masks in Python. */
  function computeMasks(columns, controls, settings, n) {
    const subset = new Uint8Array(n).fill(1);
    const pass = new Uint8Array(n).fill(1);
    for (const control of controls) {
      const column = columns[control.column];
      const setting = settings[control.column] || {};
      if (control.kind === 'threshold') {
        const low = 'min' in setting ? setting.min : control.min;
        const high = 'max' in setting ? setting.max : control.max;
        const values = column.values;
        for (let i = 0; i < n; i++) {
          const v = values[i];
          if (!Number.isFinite(v) || (low != null && v < low) || (high != null && v > high)) pass[i] = 0;
        }
      } else if (control.kind === 'select') {
        let chosen = 'value' in setting ? setting.value : control.value;
        if (chosen == null || chosen === '' || (Array.isArray(chosen) && !chosen.length)) continue;
        if (!Array.isArray(chosen)) chosen = [chosen];
        const codes = new Set(chosen.map((label) => column.categories.indexOf(String(label))).filter((c) => c >= 0));
        const values = column.values;
        for (let i = 0; i < n; i++) if (!codes.has(values[i])) subset[i] = 0;
      } else if (control.kind === 'toggle') {
        const on = 'value' in setting ? setting.value : control.value;
        if (!on) continue;
        const values = column.values;
        for (let i = 0; i < n; i++) if (!values[i]) subset[i] = 0;
      }
    }
    return { subset, pass };
  }

  function computeStats(subset, pass) {
    let n = 0;
    let nPass = 0;
    for (let i = 0; i < subset.length; i++) {
      if (!subset[i]) continue;
      n++;
      if (pass[i]) nPass++;
    }
    return { n, n_pass: nPass, n_fail: n - nPass, pct_pass: n ? nPass / n : 0, pct_fail: n ? (n - nPass) / n : 0 };
  }

  const SPEC = /\{(\w+)(?::(,)?(?:\.(\d+))?([fd%])?)?\}/g;

  function formatNumber(value, comma, digits, kind) {
    const group = (text) => {
      if (!comma) return text;
      const [whole, fraction] = text.split('.');
      const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      return fraction !== undefined ? `${grouped}.${fraction}` : grouped;
    };
    if (kind === '%') return `${group((value * 100).toFixed(digits ? Number(digits) : 0))}%`;
    if (kind === 'd' || (kind === undefined && digits === undefined && Number.isInteger(value))) {
      return group(String(Math.round(value)));
    }
    return group(value.toFixed(digits !== undefined ? Number(digits) : 2));
  }

  /** Python-style "{n_pass:,} of {n:,} ({pct_pass:.1%})" with the subset of specs explorer.py allows. */
  function formatTemplate(template, values) {
    return template.replace(SPEC, (match, name, comma, digits, kind) =>
      name in values ? formatNumber(values[name], comma, digits, kind) : match
    );
  }

  /** Bin edges over the finite (and, with log, positive) values selected by mask. */
  function binEdges(values, mask, bins, log) {
    let low = Infinity;
    let high = -Infinity;
    for (let i = 0; i < values.length; i++) {
      if (mask && !mask[i]) continue;
      let v = values[i];
      if (!Number.isFinite(v) || (log && v <= 0)) continue;
      if (log) v = Math.log10(v);
      if (v < low) low = v;
      if (v > high) high = v;
    }
    if (low === Infinity) return [];
    if (high === low) high = low + 1;
    const edges = [];
    for (let i = 0; i <= bins; i++) edges.push(low + ((high - low) * i) / bins);
    return edges;
  }

  /** Counts per bin of values selected by mask (bins are [e_i, e_i+1), the last one closed). */
  function binCounts(values, mask, edges, log) {
    const bins = edges.length - 1;
    const counts = new Array(Math.max(bins, 0)).fill(0);
    if (bins < 1) return counts;
    const low = edges[0];
    const width = (edges[bins] - low) / bins;
    for (let i = 0; i < values.length; i++) {
      if (mask && !mask[i]) continue;
      let v = values[i];
      if (!Number.isFinite(v) || (log && v <= 0)) continue;
      if (log) v = Math.log10(v);
      let bin = Math.floor((v - low) / width);
      if (bin === bins && v <= edges[bins]) bin = bins - 1;
      if (bin >= 0 && bin < bins) counts[bin]++;
    }
    return counts;
  }

  /** A fixed pseudo-random order of 0..n-1 (mulberry32), so subsampled points stay put as filters move. */
  function permutation(n, seed = 1) {
    const order = new Uint32Array(n);
    for (let i = 0; i < n; i++) order[i] = i;
    let state = seed >>> 0;
    const random = () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      const swap = order[i];
      order[i] = order[j];
      order[j] = swap;
    }
    return order;
  }

  /** Up to `max` indices of rows selected by mask, in a stable random order, then sorted. */
  function sampleIndices(order, mask, max) {
    const picked = [];
    for (let k = 0; k < order.length && picked.length < max; k++) if (mask[order[k]]) picked.push(order[k]);
    return picked.sort((a, b) => a - b);
  }

  /** Settings as the agent reads them: {column: {min, max} | {value}}. */
  function settingsFor(controls, settings) {
    const out = {};
    for (const control of controls) {
      const setting = settings[control.column] || {};
      if (control.kind === 'threshold') {
        const entry = {};
        const low = 'min' in setting ? setting.min : control.min;
        const high = 'max' in setting ? setting.max : control.max;
        if (low != null) entry.min = low;
        if (high != null) entry.max = high;
        out[control.column] = entry;
      } else {
        out[control.column] = { value: 'value' in setting ? setting.value : control.value ?? null };
      }
    }
    return out;
  }

  function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  /** Deep merge: later objects win; arrays are replaced, not merged. */
  function merge(...objects) {
    const out = {};
    for (const object of objects) {
      if (!object) continue;
      for (const [key, value] of Object.entries(object)) {
        out[key] = isPlainObject(value) && isPlainObject(out[key]) ? merge(out[key], value) : value;
      }
    }
    return out;
  }

  function toggleTheme(current) {
    return current === 'light' ? 'dark' : 'light';
  }

  const helpers = {
    columnViews,
    computeMasks,
    computeStats,
    formatTemplate,
    binEdges,
    binCounts,
    permutation,
    sampleIndices,
    settingsFor,
    merge,
    toggleTheme
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = helpers;
  if (typeof document === 'undefined') return;

  // -- page -----------------------------------------------------------------------

  const root = document.querySelector('.cf-report');
  if (!root) return;
  root.classList.add('cf-js');

  const themeElement = document.getElementById('cf-theme');
  const themes = themeElement ? JSON.parse(themeElement.textContent) : {};
  const STORAGE_KEY = 'storyteller-theme';
  const host = global.cfHost && typeof global.cfHost.send === 'function' ? global.cfHost : null;
  if (host) root.classList.add('cf-hosted');

  function storage(action, value) {
    try {
      if (action === 'get') return global.localStorage.getItem(STORAGE_KEY);
      if (value) global.localStorage.setItem(STORAGE_KEY, value);
      else global.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* private windows and sandboxed frames have no storage */
    }
    return null;
  }

  const prefersDark = global.matchMedia ? global.matchMedia('(prefers-color-scheme: dark)') : null;
  let printing = false;

  function mode() {
    if (printing) return 'light';
    const chosen = root.dataset.theme;
    if (chosen === 'light' || chosen === 'dark') return chosen;
    return prefersDark && prefersDark.matches ? 'dark' : 'light';
  }

  function palette() {
    return themes[mode()] || themes.light || { plotly: {}, categorical: [], neutral: '#999', ink: '#000', pass: '#000' };
  }

  function applyTheme(choice) {
    if (choice === 'light' || choice === 'dark') root.dataset.theme = choice;
    else delete root.dataset.theme;
    const button = root.querySelector('[data-cf-action="theme"]');
    if (button) button.dataset.state = choice || mode();
    redrawAll();
  }

  // -- plotly blocks ----------------------------------------------------------------

  const Plotly = () => global.Plotly;
  const PLOT_CONFIG = {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'],
    toImageButtonOptions: { format: 'svg' }
  };

  function readJson(id) {
    const element = document.getElementById(id);
    return element ? JSON.parse(element.textContent) : null;
  }

  const plots = new Set();

  function drawPlotly(element) {
    if (!Plotly()) return;
    const spec = readJson(element.dataset.cfSpec);
    if (!spec) return;
    const height = Number(element.dataset.height) || 420;
    const layout = merge(palette().plotly, spec.layout, { height, autosize: true }, printSize(element));
    Plotly().react(element, spec.data, layout, PLOT_CONFIG);
    plots.add(element);
  }

  // -- explorers --------------------------------------------------------------------

  const explorers = new Map();

  async function decodeData(id, columns) {
    const element = document.getElementById(id);
    if (!element) throw new Error(`missing data ${id}`);
    const binary = atob(element.textContent.trim());
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    const buffer = await new Response(stream).arrayBuffer();
    return columnViews(buffer, columns);
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  function valueText(column, i) {
    if (column.kind === 'cat') return column.values[i] === MISSING_CODE ? '' : column.categories[column.values[i]];
    if (column.kind === 'u8') return column.values[i] ? 'yes' : 'no';
    const v = column.values[i];
    if (!Number.isFinite(v)) return '';
    return Math.abs(v) >= 1000 || Number.isInteger(v) ? Math.round(v).toLocaleString('en-US') : v.toPrecision(3);
  }

  async function setupExplorer(element) {
    const spec = readJson(element.dataset.cfSpec);
    if (!spec) return;
    let columns;
    try {
      columns = await decodeData(element.dataset.cfData, spec.columns);
    } catch (error) {
      element.querySelector('.cf-live')?.insertAdjacentHTML(
        'afterbegin',
        `<p class="cf-error">Could not load the data: ${escapeHtml(error.message)}</p>`
      );
      return;
    }
    const state = { spec, columns, settings: {}, order: null, element, frame: 0 };
    explorers.set(element, state);
    wireControls(state);
    renderExplorer(state);
    element.classList.add('cf-ready');
  }

  function wireControls(state) {
    const { element } = state;
    for (const input of element.querySelectorAll('[data-cf-bound]')) {
      const column = input.closest('[data-cf-column]').dataset.cfColumn;
      const bound = input.dataset.cfBound;
      input.addEventListener('input', () => {
        const value = Number(input.value);
        if (!Number.isFinite(value)) return;
        setSetting(state, column, { [bound]: value });
      });
    }
    for (const select of element.querySelectorAll('select[data-cf-select]')) {
      const column = select.dataset.cfSelect;
      select.addEventListener('change', () => {
        const values = [...select.selectedOptions].map((o) => o.value).filter((v) => v !== '');
        setSetting(state, column, { value: select.multiple ? values : values[0] ?? null });
      });
    }
    for (const box of element.querySelectorAll('input[data-cf-toggle]')) {
      box.addEventListener('change', () => setSetting(state, box.dataset.cfToggle, { value: box.checked }));
    }
    element.querySelector('[data-cf-action="reset"]')?.addEventListener('click', () => {
      state.settings = {};
      syncInputs(state);
      schedule(state);
    });
    element.querySelector('[data-cf-action="copy"]')?.addEventListener('click', (event) => {
      copyText(JSON.stringify(settingsFor(state.spec.controls, state.settings), null, 2), event.currentTarget);
    });
    element.querySelector('[data-cf-action="send"]')?.addEventListener('click', () => {
      if (!host) return;
      const title = state.spec.title ? ` "${state.spec.title}"` : '';
      host.send(
        `Settings chosen in the report explorer${title}:\n${JSON.stringify(settingsFor(state.spec.controls, state.settings), null, 2)}`
      );
    });
  }

  function setSetting(state, column, patch) {
    state.settings[column] = { ...(state.settings[column] || {}), ...patch };
    syncInputs(state);
    schedule(state);
  }

  /** Keeps paired range and number inputs, and dragged threshold lines, in step with the settings. */
  function syncInputs(state) {
    const { element, spec, settings } = state;
    for (const control of spec.controls) {
      const setting = settings[control.column] || {};
      const box = element.querySelector(`[data-cf-column="${CSS.escape(control.column)}"]`);
      if (control.kind === 'threshold' && box) {
        for (const bound of ['min', 'max']) {
          const value = bound in setting ? setting[bound] : control[bound];
          if (value == null) continue;
          for (const input of box.querySelectorAll(`[data-cf-bound="${bound}"]`)) {
            if (document.activeElement !== input) input.value = String(roundTo(value, Number(input.step) || 0));
          }
        }
      } else if (control.kind === 'select') {
        const select = element.querySelector(`select[data-cf-select="${CSS.escape(control.column)}"]`);
        const value = 'value' in setting ? setting.value : control.value;
        if (select) {
          const chosen = new Set(value == null ? [''] : Array.isArray(value) ? value : [value]);
          for (const option of select.options) option.selected = chosen.has(option.value);
        }
      } else if (control.kind === 'toggle') {
        const box = element.querySelector(`input[data-cf-toggle="${CSS.escape(control.column)}"]`);
        if (box) box.checked = Boolean('value' in setting ? setting.value : control.value);
      }
    }
  }

  function roundTo(value, step) {
    if (!step) return value;
    const digits = Math.max(0, Math.ceil(-Math.log10(step)));
    return Number(value.toFixed(Math.min(digits, 12)));
  }

  function schedule(state) {
    if (state.frame) return;
    state.frame = requestAnimationFrame(() => {
      state.frame = 0;
      renderExplorer(state);
    });
  }

  function thresholdsOn(spec, settings, column) {
    const out = [];
    for (const control of spec.controls) {
      if (control.kind !== 'threshold' || control.column !== column) continue;
      const setting = settings[column] || {};
      for (const bound of ['min', 'max']) {
        const value = bound in setting ? setting[bound] : control[bound];
        if (value != null) out.push({ bound, value });
      }
    }
    return out;
  }

  function thresholdShapes(lines, axis, log, colors) {
    return lines.map(({ value }) => {
      const at = log ? Math.log10(Math.max(value, 1e-12)) : value;
      return axis === 'x'
        ? { type: 'line', x0: at, x1: at, y0: 0, y1: 1, yref: 'paper', editable: true, line: { color: colors.ink, width: 2, dash: 'dash' } }
        : { type: 'line', y0: at, y1: at, x0: 0, x1: 1, xref: 'paper', editable: true, line: { color: colors.ink, width: 2, dash: 'dash' } };
    });
  }

  function renderExplorer(state) {
    const { spec, columns, settings, element } = state;
    const n = spec.n;
    const { subset, pass } = computeMasks(columns, spec.controls, settings, n);
    const passing = new Uint8Array(n);
    for (let i = 0; i < n; i++) passing[i] = subset[i] && pass[i];
    const shown = spec.mode === 'filter' ? passing : subset;
    const stats = computeStats(subset, pass);
    const colors = palette();
    const failing = new Uint8Array(n);
    for (let i = 0; i < n; i++) failing[i] = shown[i] && !passing[i];

    spec.views.forEach((view, index) => {
      const target = element.querySelector(`[data-cf-view="${index}"]`);
      if (!target) return;
      if (view.kind === 'stat') {
        target.textContent = formatTemplate(view.template, stats);
      } else if (view.kind === 'table') {
        target.innerHTML = tableHtml(state, view, shown);
      } else if (Plotly()) {
        const figure = chartFigure(state, view, { shown, passing, failing, colors });
        if (!figure) return;
        const layout = merge(
          colors.plotly,
          figure.layout,
          { height: Number(target.dataset.height) || 300, autosize: true },
          printSize(target)
        );
        Plotly().react(target, figure.data, layout, { ...PLOT_CONFIG, edits: { shapePosition: true } });
        plots.add(target);
        if (!target.dataset.cfWired) {
          target.dataset.cfWired = '1';
          target.on?.('plotly_relayout', (event) => onShapeMoved(state, view, event));
        }
      }
    });
    for (const summary of element.querySelectorAll('[data-cf-settings]')) {
      summary.textContent = settingsLine(spec.controls, settings);
    }
  }

  function settingsLine(controls, settings) {
    const parts = [];
    for (const [column, entry] of Object.entries(settingsFor(controls, settings))) {
      const control = controls.find((c) => c.column === column);
      const name = control.label || column;
      if (control.kind === 'threshold') {
        if (entry.min != null && entry.max != null) parts.push(`${fmt(entry.min)} ≤ ${name} ≤ ${fmt(entry.max)}`);
        else if (entry.min != null) parts.push(`${name} ≥ ${fmt(entry.min)}`);
        else if (entry.max != null) parts.push(`${name} ≤ ${fmt(entry.max)}`);
      } else if (control.kind === 'select') {
        const value = entry.value;
        parts.push(`${name}: ${value == null || (Array.isArray(value) && !value.length) ? 'all' : [].concat(value).join(', ')}`);
      } else {
        parts.push(`${name}: ${entry.value ? 'on' : 'off'}`);
      }
    }
    return parts.join(' · ');
  }

  function fmt(value) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(4)));
  }

  /** A dragged threshold line (plotly "shapes[i].x0") moves the control it belongs to. */
  function onShapeMoved(state, view, event) {
    const keys = Object.keys(event || {}).filter((key) => /^shapes\[\d+\]\.(x0|y0)$/.test(key));
    if (!keys.length) return;
    for (const key of keys) {
      const [, index, axis] = key.match(/^shapes\[(\d+)\]\.(x|y)0$/);
      const column = view.kind === 'histogram' ? view.column : axis === 'x' ? view.x : view.y;
      // Only histograms put log10 values on a linear axis; scatter log axes report data units.
      const log = view.kind === 'histogram' && view.log_x;
      const lines = thresholdsOn(state.spec, state.settings, column);
      // Shapes are listed x-lines first, then y-lines (see chartFigure).
      const offset = view.kind === 'scatter' && axis === 'y' ? thresholdsOn(state.spec, state.settings, view.x).length : 0;
      const line = lines[Number(index) - offset];
      if (!line) continue;
      let value = Number(event[key]);
      if (log) value = 10 ** value;
      setSetting(state, column, { [line.bound]: value });
    }
  }

  function chartFigure(state, view, { shown, passing, failing, colors }) {
    const { columns, spec, settings } = state;
    if (view.kind === 'histogram') {
      const values = columns[view.column].values;
      const edges = binEdges(values, shown, view.bins || 60, view.log_x);
      if (!edges.length) return { data: [], layout: { title: { text: view.title || view.column } } };
      const centers = edges.slice(0, -1).map((edge, i) => (edge + edges[i + 1]) / 2);
      const width = edges[1] - edges[0];
      const good = binCounts(values, passing, edges, view.log_x);
      const bad = binCounts(values, failing, edges, view.log_x);
      const lines = thresholdsOn(spec, settings, view.column);
      return {
        data: [
          { type: 'bar', x: centers, y: good, width, name: 'passes', marker: { color: colors.pass }, hovertemplate: '%{y:,} rows<extra>passes</extra>' },
          { type: 'bar', x: centers, y: bad, width, name: 'fails', marker: { color: colors.neutral }, hovertemplate: '%{y:,} rows<extra>fails</extra>' }
        ],
        layout: {
          title: { text: view.title || labelOf(spec, view.column) },
          barmode: 'stack',
          bargap: 0,
          showlegend: false,
          xaxis: { title: { text: view.log_x ? `log₁₀ ${labelOf(spec, view.column)}` : labelOf(spec, view.column) } },
          yaxis: { title: { text: 'rows' }, type: view.log_y ? 'log' : 'linear' },
          shapes: thresholdShapes(lines, 'x', view.log_x, colors), // the axis shows log10 values
          margin: { t: 36, r: 12, b: 44, l: 52 }
        }
      };
    }
    if (view.kind === 'scatter') {
      if (!state.order) state.order = permutation(spec.n, 7);
      const index = sampleIndices(state.order, shown, view.max_points || 20000);
      const x = columns[view.x].values;
      const y = columns[view.y].values;
      const size = index.length > 5000 ? 3 : 5;
      const trace = (rows, name, color, extra) => ({
        type: 'scatter',
        mode: 'markers',
        x: rows.map((i) => x[i]),
        y: rows.map((i) => y[i]),
        name,
        marker: { color, size, ...((extra && extra.marker) || {}) },
        hovertemplate: `${escapeHtml(view.x)} %{x}<br>${escapeHtml(view.y)} %{y}<extra>${escapeHtml(name)}</extra>`
      });
      let data;
      if (view.color === 'pass') {
        data = [trace(index.filter((i) => !passing[i]), 'fails', colors.neutral), trace(index.filter((i) => passing[i]), 'passes', colors.pass)];
      } else if (view.color && columns[view.color]) {
        const column = columns[view.color];
        if (column.kind === 'f32') {
          const t = trace(index, view.color, null);
          t.marker = { size, color: index.map((i) => column.values[i]), colorscale: colors.plotly.colorscale?.sequential, showscale: true, colorbar: { title: { text: view.color }, thickness: 12 } };
          data = [t];
        } else {
          const groups = new Map();
          for (const i of index) {
            const label = valueText(column, i);
            if (!groups.has(label)) groups.set(label, []);
            groups.get(label).push(i);
          }
          const labels = [...groups.keys()].sort();
          const palette = colors.categorical;
          const named = labels.slice(0, palette.length);
          data = named.map((label, k) => trace(groups.get(label), label || '(missing)', palette[k]));
          const rest = labels.slice(palette.length).flatMap((label) => groups.get(label));
          if (rest.length) data.push(trace(rest, 'other', colors.neutral));
        }
      } else {
        data = [trace(index, view.y, colors.pass)];
      }
      const shapes = [
        // Plotly log axes take shape positions in data units.
        ...thresholdShapes(thresholdsOn(spec, settings, view.x), 'x', false, colors),
        ...thresholdShapes(thresholdsOn(spec, settings, view.y), 'y', false, colors)
      ];
      const sampled = index.length < countOf(shown) ? ` (${index.length.toLocaleString('en-US')} of ${countOf(shown).toLocaleString('en-US')} points)` : '';
      return {
        data,
        layout: {
          title: { text: `${escapeHtml(view.title || `${view.y} vs ${view.x}`)}${sampled}` },
          xaxis: { title: { text: view.x }, type: view.log_x ? 'log' : 'linear', showgrid: true },
          yaxis: { title: { text: view.y }, type: view.log_y ? 'log' : 'linear' },
          shapes,
          legend: { orientation: 'h', y: -0.2 },
          margin: { t: 36, r: 12, b: 44, l: 52 }
        }
      };
    }
    if (view.kind === 'bar') {
      const column = columns[view.column];
      const counts = new Map();
      for (let i = 0; i < spec.n; i++) {
        if (!shown[i]) continue;
        const label = valueText(column, i);
        const entry = counts.get(label) || [0, 0];
        entry[passing[i] ? 0 : 1]++;
        counts.set(label, entry);
      }
      const labels = [...counts.keys()].sort();
      const series = (k, name, color) =>
        view.horizontal
          ? { type: 'bar', orientation: 'h', y: labels, x: labels.map((l) => counts.get(l)[k]), name, marker: { color } }
          : { type: 'bar', x: labels, y: labels.map((l) => counts.get(l)[k]), name, marker: { color } };
      return {
        data: [series(0, 'passes', colors.pass), series(1, 'fails', colors.neutral)],
        layout: {
          title: { text: view.title || `rows per ${view.column}` },
          barmode: 'stack',
          legend: { orientation: 'h', y: -0.2 },
          xaxis: { type: 'category' },
          margin: { t: 36, r: 12, b: 44, l: 52 }
        }
      };
    }
    if (view.kind === 'box') {
      const values = columns[view.column].values;
      const groups = new Map();
      for (let i = 0; i < spec.n; i++) {
        if (!shown[i] || !Number.isFinite(values[i])) continue;
        const label = view.by ? valueText(columns[view.by], i) : view.column;
        if (!groups.has(label)) groups.set(label, []);
        groups.get(label).push(values[i]);
      }
      const labels = [...groups.keys()].sort();
      return {
        data: labels.map((label, k) => ({
          type: 'box',
          y: groups.get(label),
          name: label,
          boxpoints: false,
          marker: { color: colors.categorical[k] || colors.neutral }
        })),
        layout: {
          title: { text: view.title || (view.by ? `${view.column} by ${view.by}` : view.column) },
          showlegend: false,
          yaxis: { title: { text: view.column }, type: view.log_y ? 'log' : 'linear' },
          margin: { t: 36, r: 12, b: 44, l: 52 }
        }
      };
    }
    return null;
  }

  function labelOf(spec, column) {
    const control = spec.controls.find((c) => c.column === column && c.label);
    return control ? control.label : column;
  }

  function countOf(mask) {
    let n = 0;
    for (let i = 0; i < mask.length; i++) n += mask[i];
    return n;
  }

  function tableHtml(state, view, shown) {
    const { columns, spec } = state;
    const names = view.columns && view.columns.length ? view.columns : spec.columns.map((c) => c.name);
    const rows = [];
    for (let i = 0; i < spec.n && rows.length < (view.n || 20); i++) if (shown[i]) rows.push(i);
    const head = names.map((name) => `<th scope="col"${columns[name].kind === 'f32' ? ' class="cf-num"' : ''}>${escapeHtml(name)}</th>`).join('');
    const body = rows
      .map((i) => `<tr>${names.map((name) => `<td${columns[name].kind === 'f32' ? ' class="cf-num"' : ''}>${escapeHtml(valueText(columns[name], i))}</td>`).join('')}</tr>`)
      .join('');
    const total = countOf(shown);
    return `<div class="cf-table-wrap"><table class="cf-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div><p class="cf-caption">First ${rows.length.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} rows shown.</p>`;
  }

  // -- small conveniences ------------------------------------------------------------------

  function copyText(text, button) {
    const done = () => {
      if (!button) return;
      const label = button.textContent;
      button.textContent = 'Copied';
      setTimeout(() => (button.textContent = label), 1400);
    };
    if (navigator.clipboard && global.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
    } else fallbackCopy(text, done);
  }

  function fallbackCopy(text, done) {
    const area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
      done();
    } catch {
      /* nothing else to try */
    }
    area.remove();
  }

  function sortTable(header) {
    const table = header.closest('table');
    const column = [...header.parentNode.children].indexOf(header);
    const direction = header.dataset.sort === 'asc' ? 'desc' : 'asc';
    for (const th of header.parentNode.children) delete th.dataset.sort;
    header.dataset.sort = direction;
    const body = table.tBodies[0];
    const key = (row) => {
      const cell = row.children[column];
      if (!cell) return '';
      return cell.dataset.v !== undefined ? Number(cell.dataset.v) : cell.textContent.trim().toLowerCase();
    };
    const rows = [...body.rows].sort((a, b) => {
      const ka = key(a);
      const kb = key(b);
      const order = typeof ka === 'number' && typeof kb === 'number' ? ka - kb : String(ka).localeCompare(String(kb), undefined, { numeric: true });
      return direction === 'asc' ? order : -order;
    });
    body.append(...rows);
  }

  function openLightbox(image) {
    let dialog = document.getElementById('cf-lightbox');
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.id = 'cf-lightbox';
      dialog.className = 'cf-lightbox';
      dialog.innerHTML = '<img alt=""><button type="button" class="cf-button" aria-label="Close">Close</button>';
      dialog.addEventListener('click', () => dialog.close());
      root.appendChild(dialog);
    }
    const copy = dialog.querySelector('img');
    copy.src = image.src;
    copy.alt = image.alt;
    dialog.showModal();
  }

  // -- whole page: init, theme, print, toc ----------------------------------------------------

  function init(scope) {
    for (const element of scope.querySelectorAll('[data-cf-plotly]')) drawPlotly(element);
    for (const element of scope.querySelectorAll('[data-cf-explorer]')) {
      if (!explorers.has(element)) setupExplorer(element);
    }
  }

  function redrawAll() {
    for (const element of document.querySelectorAll('[data-cf-plotly]')) drawPlotly(element);
    for (const state of explorers.values()) renderExplorer(state);
  }

  function teardown(scope) {
    for (const element of scope.querySelectorAll('.js-plotly-plot')) {
      Plotly()?.purge(element);
      plots.delete(element);
    }
    for (const element of scope.querySelectorAll('[data-cf-explorer]')) explorers.delete(element);
  }

  // Print: light theme, open details, plots sized to the printed column. Plotly draws
  // at fixed pixel sizes, so each plot gets an explicit width while printing: first
  // estimated from its share of the screen column (beforeprint comes before the print
  // layout), then measured again once the print media query applies.
  const PRINT_WIDTH = 680; // A4 or Letter minus the page margins, in CSS pixels
  let reopened = [];

  function printSize(element) {
    const width = Number(element.dataset.cfPrintWidth);
    return printing && width ? { width, autosize: false } : null;
  }

  function sizeForPrint(measure) {
    const sheet = root.querySelector('.cf-sheet');
    const column = sheet ? sheet.clientWidth : PRINT_WIDTH;
    for (const element of plots) {
      if (!element.isConnected) continue;
      // The plot's own box: a block (or grid cell) as wide as the space it is given.
      const width = measure
        ? Math.min(element.clientWidth, PRINT_WIDTH)
        : Math.round((element.clientWidth / Math.max(column, 1)) * PRINT_WIDTH);
      element.dataset.cfPrintWidth = String(Math.max(200, width));
    }
    redrawAll();
  }

  function beforePrint() {
    if (printing) return;
    printing = true;
    reopened = [...root.querySelectorAll('details:not([open])')];
    for (const details of reopened) details.open = true;
    sizeForPrint(false);
  }

  function afterPrint() {
    if (!printing) return;
    printing = false;
    for (const details of reopened) details.open = false;
    reopened = [];
    for (const element of plots) delete element.dataset.cfPrintWidth;
    redrawAll();
  }

  function onPrintMedia(event) {
    if (!event.matches) {
      afterPrint();
      return;
    }
    beforePrint();
    sizeForPrint(true);
  }

  function setupToc() {
    const links = new Map([...root.querySelectorAll('.cf-toc a[href^="#"]')].map((a) => [a.getAttribute('href').slice(1), a]));
    if (!links.size || !global.IntersectionObserver) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          for (const link of links.values()) link.classList.remove('cf-current');
          links.get(entry.target.id)?.classList.add('cf-current');
        }
      },
      { rootMargin: '0px 0px -70% 0px' }
    );
    for (const id of links.keys()) {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    }
  }

  // -- cross-references --------------------------------------------------------------------

  /** Opens the tabs around a link target so the browser can scroll to it, and flashes it. */
  function reveal(id) {
    const target = id && document.getElementById(id);
    if (!target || !root.contains(target)) return;
    for (let panel = target.closest('.cf-tab-panel'); panel; panel = panel.parentElement.closest('.cf-tab-panel')) {
      const index = [...panel.parentElement.children].indexOf(panel);
      const radio = panel.closest('.cf-tabs').querySelectorAll(':scope > input')[index];
      if (radio) radio.checked = true;
    }
    target.classList.remove('cf-changed');
    void target.offsetWidth; // restart the animation
    target.classList.add('cf-changed');
    setTimeout(() => target.classList.remove('cf-changed'), 2400);
  }

  // -- live updates ------------------------------------------------------------------------

  /** Replaces the parts of the page whose content hash changed; keeps the rest (plots, settings, scroll). */
  function patch(html) {
    const next = new DOMParser().parseFromString(html, 'text/html');
    const nextRoot = next.querySelector('.cf-report');
    if (!nextRoot) return false;
    // A new library (e.g. the first plotly chart) needs its script to run: reload instead.
    for (const library of next.querySelectorAll('script[data-cf-lib]')) {
      if (!document.querySelector(`script[data-cf-lib="${library.dataset.cfLib}"]`)) return false;
    }
    const parts = (scope) => [...scope.querySelectorAll('[data-cf-part]')];
    const current = new Map(parts(root).map((element) => [element.dataset.cfPart, element]));
    for (const incoming of parts(nextRoot)) {
      const existing = current.get(incoming.dataset.cfPart);
      if (existing && existing.dataset.cfHash === incoming.dataset.cfHash) continue;
      const imported = document.importNode(incoming, true);
      if (existing) {
        teardown(existing);
        existing.replaceWith(imported);
      } else {
        const parent = root.querySelector(`[data-cf-slot="${incoming.parentElement.dataset.cfSlot}"]`);
        if (!parent) return false;
        const before = incoming.nextElementSibling && current.get(incoming.nextElementSibling.dataset.cfPart);
        parent.insertBefore(imported, before || null);
      }
      if (imported.matches('section, .cf-block')) {
        imported.classList.add('cf-changed');
        setTimeout(() => imported.classList.remove('cf-changed'), 2400);
      }
      init(imported);
    }
    const kept = new Set(parts(nextRoot).map((element) => element.dataset.cfPart));
    for (const [key, element] of current) {
      if (!kept.has(key) && element.isConnected) {
        teardown(element);
        element.remove();
      }
    }
    for (const id of ['cf-theme', 'cf-comments']) {
      const fresh = next.getElementById(id);
      const old = document.getElementById(id);
      if (fresh && old) old.textContent = fresh.textContent;
    }
    document.title = next.title;
    setupToc();
    global.cfComments?.refresh(); // highlights again, in the new text
    return true;
  }

  function listenForUpdates() {
    if (host) {
      global.addEventListener('message', (event) => {
        const message = event.data;
        if (!message || message.type !== 'cf-update' || typeof message.html !== 'string') return;
        if (!patch(message.html)) host.reload?.();
      });
    }
    const live = document.querySelector('meta[name="cf-live"]');
    if (live && global.EventSource && /^https?:$/.test(location.protocol)) {
      const events = new EventSource(live.content || '/events');
      events.addEventListener('update', async () => {
        const response = await fetch(location.href, { cache: 'no-store' });
        if (!patch(await response.text())) location.reload();
      });
    }
  }

  // -- start -------------------------------------------------------------------------------

  applyTheme(storage('get'));
  prefersDark?.addEventListener?.('change', () => !root.dataset.theme && applyTheme(null));
  root.addEventListener('click', (event) => {
    const action = event.target.closest('[data-cf-action]');
    if (action && action.dataset.cfAction === 'theme') {
      const next = toggleTheme(mode());
      storage('set', next);
      applyTheme(next);
      return;
    }
    if (action && action.dataset.cfAction === 'print') {
      global.print();
      return;
    }
    const header = event.target.closest('table[data-cf-sortable] thead th');
    if (header) {
      sortTable(header);
      return;
    }
    const ref = event.target.closest('a.cf-ref[href^="#"]');
    if (ref) {
      // Before the browser follows the link, so it can scroll to a block in a closed tab.
      reveal(decodeURIComponent(ref.getAttribute('href').slice(1)));
      return;
    }
    const image = event.target.closest('.cf-figure img');
    if (image && typeof HTMLDialogElement !== 'undefined') openLightbox(image);
  });
  global.addEventListener('beforeprint', beforePrint);
  global.addEventListener('afterprint', afterPrint);
  global.matchMedia?.('print').addEventListener?.('change', onPrintMedia);
  init(document);
  setupToc();
  listenForUpdates();
  if (location.hash.length > 1) {
    reveal(decodeURIComponent(location.hash.slice(1)));
    document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
  }
  global.cfReport = { patch, settings: () => [...explorers.values()].map((s) => settingsFor(s.spec.controls, s.settings)) };
})(typeof window !== 'undefined' ? window : globalThis);
