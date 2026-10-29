"""Fills the "composition" section and finalizes the report."""

import storyteller as sty
from storyteller.demo import simulated_cells

cells = simulated_cells()
kept = cells[(cells.n_genes >= 500) & (cells.pct_mt <= 15)]
report = sty.Report.open("reports/pbmc")

with report.section("composition", "Cell type composition"):
    counts = kept.groupby(["cell_type", "sample"], observed=True).size().unstack("sample")
    report.table(counts, caption="Cells per type and donor after QC.")

report.summary(f"**{len(kept):,} of {len(cells):,} cells** pass QC.")
report.metrics({"Cells": len(cells), "After QC": len(kept)})
report.finalize()
