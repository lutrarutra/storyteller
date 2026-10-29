---
name: storyteller
description: Build the analysis report as one standalone HTML file with storyteller (Python), live, section by section while the analysis runs. Use for every final report, anything the user will share, archive or print (PDF), and whenever the user should judge thresholds or filters themselves (interactive explorer). Not for showing a single quick figure: show the PNG with codeflower-show instead.
---

# Reports with storyteller

`storyteller` writes a single HTML file with everything inside (images, charts,
data, fonts, styles), in code flower's look. It works offline, prints to PDF, and keeps
working as a plain page where scripts are blocked. You build it **while you analyse**:
each script fills its own section, and the user watches the report grow in VS Code.

It is installed in the project venv (editable, see "The library itself").

## The workflow

```python
# scripts/00_report.py: run once at the start; lay out the report
import storyteller as sty

r = sty.Report.open("reports/pbmc", title="PBMC cell types", subtitle="2 donors, 10x 3'")
r.pending("qc", "Quality control", note="Filtering damaged cells and doublets")
r.pending("clusters", "Clustering and annotation")
r.pending("limits", "Limitations")
```

```sh
python scripts/00_report.py
codeflower-show reports/pbmc.html --message "The report fills in as the analysis runs"
```

```python
# scripts/01_qc.py: each analysis script fills its own section
import storyteller as sty
sty.theme.use()                         # matplotlib + plotly in the report's colours

r = sty.Report.open("reports/pbmc")     # the same report; no title needed
with r.section("qc", "Quality control", description="Are the cells intact?"):
    r.text("We removed cells with fewer than 500 genes or more than 15 % mitochondrial reads.")
    r.figure(fig, caption="Genes per cell; the dashed line is the cut-off.")
    r.table(per_sample, caption="Cells kept per sample.", format={"kept": "{:.1%}"})
```

At the end, in a short last script:

```python
r = sty.Report.open("reports/pbmc")
r.summary("**Eight cell types** ... (plain language, what the data supports, how sure you are)")
r.metrics({"Cells after QC": 9871, "Clusters": 12})
r.finalize()                            # drops draft marks, adds provenance, fits the size budget, checks
```

How it behaves:

- **Saved as you go.** Every block is written to `reports/pbmc.html` within half a second;
  the VS Code tab reloads by itself. The store `reports/pbmc.report/` holds the parts. Keep it.
- **Re-running replaces.** A `with r.section("qc", ...)` block replaces that section's
  whole content each time it runs, in place. Re-run a script after a fix; never delete
  sections by hand to "start over".
- **Failures show.** If the code inside a section raises, the section shows the error
  (and your script still fails). Fix and re-run.
- **Several scripts, one report.** Each script opens the report and writes its own
  sections; they can even run at the same time.
- `storyteller outline reports/pbmc` prints the sections, their keys, status and
  the script that wrote them. Check it before adding a section, to reuse its key.
- `storyteller remove reports/pbmc <key>` removes a section.

## Blocks

| Call | Use for |
|---|---|
| `r.summary(md)` | The findings, first. One per report. |
| `r.metrics({...})` or `[sty.Metric(label, value, note=, status="good"/"warning"/"critical")]` | 2–6 headline numbers. |
| `r.text(md)` | Markdown prose. `![alt](figures/x.png)` images are embedded. |
| `r.figure(obj, caption)` | matplotlib figure or axes, seaborn grid, PIL image, array, image path. Plotly figures go interactive. |
| `r.plotly(fig, caption, static=mpl_fig)` | A chart the reader should hover or zoom (embeddings, dense scatter). |
| `r.table(df, caption, format=, flags=)` | Results tables. Shows `max_rows=50`. `flags` highlights cells (booleans per row, a boolean frame, or `f(row, col)`). |
| `r.explorer(df, controls, views)` | Choices the user should judge: thresholds, filters (see below). |
| `r.callout(kind, md)` | `note`, `tip`, `success`, `warning`, `critical`: caveats, decisions, problems. |
| `r.code(path_or_text, collapsed=True)` | Key commands or a script in Methods. |
| `with r.grid(2):` | Two (or more) blocks side by side. |
| `with r.tabs(): with r.tab("A"):` | Alternative views of one thing (per sample, per method). |
| `r.provenance()` | Software versions and scripts; `finalize()` adds it. |

Figures: make them with `sty.theme.use()` active, at their final size (`figsize` about
7 × 4 in for full width, 3.5 × 3 in a grid). Small figures are embedded as SVG and large ones as
PNG at 2× density. Big photos and micrographs are resized to 2000 px and compressed.
`r.figure` closes matplotlib figures for you.

## Interactive explorer

Use it when a number in the report depends on a cut-off the user may want to set
differently, such as QC thresholds, a p-value or fold-change cut-off, or a score threshold:

```python
r.explorer(
    adata.obs,                                     # a DataFrame (or dict of columns)
    controls=[
        sty.Threshold("n_genes_by_counts", min=500, label="genes per cell"),
        sty.Threshold("pct_counts_mt", max=15, label="% mitochondrial"),
        sty.Select("sample"),                      # restrict to one sample, or all
    ],
    views=[
        sty.Stat("{n_pass:,} of {n:,} cells pass ({pct_pass:.1%})"),
        sty.Histogram("n_genes_by_counts", log_x=True),
        sty.Histogram("pct_counts_mt"),
        sty.Scatter("total_counts", "n_genes_by_counts", log_x=True, log_y=True),
        sty.Bar("sample"),                         # passing / failing per sample
    ],
    title="QC thresholds",
)
```

- `Threshold` sets pass/fail (rows pass when within all thresholds). `Select` and `Toggle`
  restrict which rows are shown. With the default `mode="highlight"`, charts show failing rows in grey.
  `mode="filter"` hides them instead.
- Other views: `Box(column, by=)` and `Table(columns, n=20)`. `Stat` fields are
  `n, n_pass, n_fail, pct_pass, pct_fail`, with format specs `,`, `.Nf`, `.N%`.
- Pass only the columns you need. Up to 200 000 rows are embedded; beyond that, a sample.
- Readers can drag threshold lines, type values, and press **Copy settings**. In VS Code's
  interactive view, **Send to chat** puts the settings in the chat box. If the user sends
  you settings, re-run the section's script with those values.
- Where scripts cannot run, the explorer shows a matplotlib snapshot at your initial
  settings. Choose initial values you can defend.

## Writing the report

- Open with the summary: what you found, in plain words, and how confident you are. Then
  data, methods (tools, versions, parameters), results, and limitations or open questions.
- Report what the data supports. Say when a result is weak or exploratory (`callout("warning", ...)`).
- Every figure and table gets a caption that says what to look at.
- Colours: keep `sty.theme.use()` and the defaults. Categories get colours in a fixed order
  (at most 8; fold the rest into "Other" or split into panels). For continuous values
  use `cmap="cf_teal"`, and for signed values `cmap="cf_div"`. The colours are colour-blind
  checked, so do not pass your own palettes.
- Look at what you made before you show it: open the figure PNGs/SVGs you embed (your
  file tool shows images), and check the HTML with `storyteller check reports/pbmc.html`.

## Size, sharing and PDF

- The report must stay under ~9.5 MB, the most VS Code previews. `finalize()` shrinks images to fit
  and prints what is large. If it is still too big, use fewer or smaller rasters, fewer
  explorer rows, or split the report.
- A Plotly chart or an explorer adds the 1.5 MB chart library once. Exotic plotly traces
  (3-D, maps, sankey) add a 5 MB library instead, so prefer matplotlib for those.
- PDF: the reader presses **Print / PDF** in the report (A4, margins, page numbers).
  You can make one with `storyteller pdf reports/pbmc.html` if playwright and Chromium are installed.
- Deliverables: copy `reports/pbmc.html` (it is the whole report) to `/data/output/<name>/`.
  Copy the PDF too if you made one. The `.report/` store stays in the workspace.

## Showing it

- `codeflower-show reports/pbmc.html --message "..."` once. The tab follows every change.
- The VS Code preview runs without scripts and shows the static snapshots. The user can
  press **Open interactive** in the tab header for live charts.
- `storyteller serve reports/pbmc` serves it with live updates at
  http://127.0.0.1:8765 for browsers outside VS Code.

## The library itself

The source is editable at `/codeflower-state/env/src/storyteller` (installed with
`pip install -e`). If you hit a bug or a missing feature that blocks the report, you may
fix it there:

- Keep the change small.
- Run its tests: `cd /codeflower-state/env/src/storyteller && python -m pytest -q`.
- Tell the user what you changed.

Your copy is kept when the sandbox image is updated, until the user resets it.
