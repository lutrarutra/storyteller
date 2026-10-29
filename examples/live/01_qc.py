"""Fills the "qc" section. Re-running it replaces the section in place."""

import matplotlib.pyplot as plt

import storyteller as sty
from storyteller.demo import simulated_cells

sty.theme.use()
cells = simulated_cells()
report = sty.Report.open("reports/pbmc")

with report.section("qc", "Quality control", description="Are the cells intact?"):
    report.explorer(
        cells,
        controls=[
            sty.Threshold("n_genes", min=500, label="genes per cell"),
            sty.Threshold("pct_mt", max=15, label="% mitochondrial"),
        ],
        views=[
            sty.Stat("{n_pass:,} of {n:,} cells pass ({pct_pass:.1%})"),
            sty.Histogram("n_genes", log_x=True),
            sty.Histogram("pct_mt"),
        ],
        title="QC thresholds",
    )
    figure, ax = plt.subplots(figsize=(7, 3.4))
    ax.scatter(cells.total_counts, cells.n_genes, s=3, c=cells.pct_mt, cmap="cf_teal")
    ax.set(xscale="log", yscale="log", xlabel="counts per cell", ylabel="genes per cell")
    report.figure(figure, caption="Counts and genes per cell, coloured by mitochondrial fraction.")
