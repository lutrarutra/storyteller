# storyteller

Standalone HTML reports for data analysis, in [code flower](https://codeflower.dev)'s look.
code flower's sandbox agent uses it to write its reports; it works anywhere Python does.

- **One file.** Images, charts, data, fonts and styles are embedded. Nothing loads from the
  network, so reports open offline, on clusters, and years later.
- **Built live.** `Report.open()` keeps the report next to its HTML and saves within half a
  second of every change. Each analysis script fills its own section, and re-running a
  script replaces its section in place.
- **Interactive where it helps.** Plotly charts, and explorers that link thresholds and
  filters to histograms, scatter plots and live counts. Each one also has a static snapshot
  for viewers that block scripts.
- **Cross-references.** `[](#key)` in text links to the figure, table or section with that
  key and reads "Figure 3"; the numbers stay right as sections are added, and the links
  work in the PDF.
- **Comments.** Readers select text, right-click and comment: a note for other readers, or
  a request for the agent. The comments are saved in the report, survive rebuilds, and
  stay with the words they are about as the report changes. The agent reads and resolves
  them with `storyteller comments`.
- **Prints well.** A4 or Letter pages with margins, page numbers and charts sized to the page.
  On wide screens the report stays a centred page with margins around it.
- **Small.** Images are resized and encoded losslessly or lossily by content, and a size
  budget shrinks them when needed.

```python
import storyteller as sty

sty.theme.use()  # matplotlib and plotly in the report's colours
report = sty.Report.open("reports/pbmc", title="PBMC cell types")
with report.section("qc", "Quality control"):
    report.text("Cells with fewer than 500 genes were removed.")
    report.figure(fig, caption="Genes per cell.")
    report.explorer(
        cells,
        controls=[sty.Threshold("n_genes", min=500), sty.Threshold("pct_mt", max=15)],
        views=[sty.Stat("{n_pass:,} of {n:,} cells pass"), sty.Histogram("n_genes"), sty.Histogram("pct_mt")],
    )
report.finalize()
```

The workflow, every block and the rules for good reports are in [skill/SKILL.md](skill/SKILL.md),
the instructions code flower's agent follows. [examples/live](examples/live) splits a report
across scripts. `storyteller demo` writes a demo report.

## Install

```sh
pip install "storyteller[plot]"     # with matplotlib, plotly, pandas, numpy
pip install "storyteller[pdf]"      # PDF export via playwright (then: playwright install chromium)
```

The only hard dependencies are Pillow and markdown-it-py. matplotlib, plotly, pandas,
numpy, polars, pygments and tifffile are used when they are installed.

## Command line

```
storyteller outline  reports/pbmc     sections, keys, status, scripts
storyteller build    reports/pbmc     re-render the HTML from the store
storyteller finalize reports/pbmc     final version: no draft marks, size budget, check
storyteller remove   reports/pbmc KEY
storyteller check    FILE.html        self-contained? small enough for VS Code (10 MB)?
storyteller pdf      FILE.html        print with headless Chromium
storyteller serve    reports/pbmc     live view in a browser on 127.0.0.1 (saves comments)
storyteller comments list reports/pbmc         readers' open comments (--all, --json)
storyteller comments reply|resolve reports/pbmc ID [MESSAGE]
storyteller comments import reports/pbmc COPY.html   comments from a copy a reader saved
storyteller palette | demo
```

## Colours

The palettes come from code flower's brand: paper `#f6efd9`, ink `#1d1d1f`, and indigo,
teal, pink and yellow accents. They were checked with an OKLab colour-vision validator
(`tests/test_palette.py`):

- **Categorical** (fixed order): `#4f46e5 #0f9d8a #d9822b #c2477a #0f8fb0 #b8930a #8b5cf6 #d6453a`.
  There is a separate set for dark mode.
- **Sequential colormaps:** `cf_teal`, `cf_indigo`, `cf_pink`.
- **Diverging colormap:** `cf_div` (teal ↔ warm grey ↔ pink).
- **Status colours:** reserved for status, never used for series.

## Development

```sh
uv venv && uv pip install -e ".[dev]"
python -m pytest -q                      # Python tests (palette checks need node)
npm ci && npm test                       # runtime.js unit tests
npx playwright-core install chromium     # once
storyteller demo -o /tmp/demo.html && npm run e2e -- /tmp/demo.html /tmp/e2e
node tests/e2e/check_live.mjs "$(which python)" /tmp/live
node tests/e2e/check_comments.mjs "$(which python)" /tmp/comments
npm run vendor:check                     # vendored JS and fonts match VERSIONS.json
```

The vendored libraries (plotly.js 4.1.2 cartesian and strict bundles, d3 7.9.0, DM Sans and
Fredoka) are fetched by `npm run vendor`, which checks each one's npm integrity hash. Their
licences are next to them in `src/storyteller/assets/`.
