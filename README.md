<p align="center">
  <img src="assets/icon.svg" width="112" height="112" alt="storyteller icon">
</p>

<h1 align="center">storyteller</h1>

<p align="center">Standalone HTML reports that tell the story of a data analysis.</p>

<p align="center">
  <a href="https://github.com/lutrarutra/codeflower-app"><img src="https://raw.githubusercontent.com/lutrarutra/codeflower-app/main/assets/icon.png" width="20" height="20" alt=""> app</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/lutrarutra/codeflower-sandbox"><img src="https://raw.githubusercontent.com/lutrarutra/codeflower-sandbox/main/assets/icon.svg" width="20" height="20" alt=""> sandbox</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/lutrarutra/codeflower-vsc-extension"><img src="https://raw.githubusercontent.com/lutrarutra/codeflower-vsc-extension/main/media/icon.svg" width="20" height="20" alt=""> VS Code extension</a>
  &nbsp;·&nbsp;
  <b><img src="assets/icon.svg" width="20" height="20" alt=""> storyteller</b>
  &nbsp;·&nbsp;
  <a href="https://github.com/lutrarutra/codeflower-tunnel"><img src="https://raw.githubusercontent.com/lutrarutra/codeflower-tunnel/main/assets/icon.svg" width="20" height="20" alt=""> tunnel</a>
  &nbsp;·&nbsp;
  <a href="https://codeflower.dev">codeflower.dev</a>
</p>

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

## How code flower fits together

The highlighted part is this repository, storyteller.

```mermaid
flowchart LR
  you(["you"])
  ghcr[("ghcr.io<br/>sandbox image")]
  subgraph front["your machine, or the cluster's login node over Remote-SSH"]
    vsc["VS Code<br/>code flower extension"]
    cli["app<br/>codeflower CLI · client window"]
  end
  subgraph node["workstation or compute node (Slurm job or any shell)"]
    subgraph sb["sandbox: Docker · Podman · Apptainer"]
      bridge["bridge"]
      agent["agent<br/>Claude Code · Codex · dsh"]
      st["storyteller"]
    end
    data[("project state<br/>/data mounts")]
  end

  you -- "starts a worker" --> cli
  you -- "chats, reads reports" --> vsc
  ghcr -- "image" --> cli
  cli -- "starts, mounts folders" --> sb
  cli -. "vscode:// connect link" .-> vsc
  vsc <== "WebSocket: ACP + _codeflower/*" ==> bridge
  bridge -- "stdio ACP" --> agent
  agent -- "runs code, reads and writes" --> data
  agent -- "writes reports with" --> st
  st -- "report.html" --> data
  style st fill:#fff1c2,stroke:#1d1d1f,stroke-width:3px,color:#1d1d1f
```

1. Start a worker with the app (`codeflower interactive`, or the client window), on a workstation or
   as a cluster job. It downloads the sandbox image once and starts it with your folders mounted.
2. The worker prints a `codeflower://` address. Paste it into the VS Code extension, or click the worker's
   link in the client window.
3. Chat with the agent in VS Code. The extension talks to the sandbox's bridge over one WebSocket; the
   bridge runs the agent, which sees only the mounted folders.
4. The agent writes its findings as storyteller reports, which open in VS Code beside the chat.

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
