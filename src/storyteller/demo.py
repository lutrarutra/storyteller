"""A demo report on simulated single-cell QC data: every block type, an explorer, live sections.

`storyteller demo -o demo.html` writes it; the examples/ folder shows the same
workflow split across scripts the way an agent would write it.
"""

from __future__ import annotations

from pathlib import Path


def simulated_cells(n: int = 6000, seed: int = 3):
    """A table of per-cell QC metrics for two samples, with a low-quality tail and some doublets."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    sample = rng.choice(["donor A", "donor B"], n, p=[0.55, 0.45])
    damaged = rng.random(n) < 0.08
    doublet = rng.random(n) < 0.04
    genes = rng.lognormal(7.2, 0.35, n) * np.where(damaged, 0.35, 1) * np.where(doublet, 1.8, 1)
    counts = genes * rng.lognormal(0.9, 0.18, n)
    mito = np.clip(rng.normal(4.0, 1.6, n) + np.where(damaged, rng.normal(14, 5, n), 0), 0, 80)
    mito += np.where(sample == "donor B", 1.0, 0.0)
    cell_type = rng.choice(
        ["T cell", "B cell", "NK cell", "Monocyte", "DC", "Platelet"], n, p=[0.42, 0.15, 0.1, 0.25, 0.04, 0.04]
    )
    return pd.DataFrame(
        {
            "sample": pd.Categorical(sample),
            "cell_type": pd.Categorical(cell_type),
            "n_genes": genes.round(),
            "total_counts": counts.round(),
            "pct_mt": mito.round(2),
            "doublet": doublet,
        }
    )


def build(path: Path) -> Path:
    import matplotlib.pyplot as plt
    import numpy as np

    import storyteller as sty

    sty.theme.use()
    cells = simulated_cells()
    report = sty.Report(
        "PBMC quality control",
        path,
        subtitle="Two donors, simulated 10x 3′ data · demo of storyteller",
        author="code flower agent",
        agent="Claude Code",
        model="Opus 5.5",
    )
    keep = (cells.n_genes >= 500) & (cells.pct_mt <= 15)
    report.summary(
        f"**{keep.sum():,} of {len(cells):,} cells** pass QC (genes ≥ 500, mitochondrial reads ≤ 15 %). "
        "Donor B has a slightly higher mitochondrial fraction ([](#mt-per-donor)); "
        "the thresholds are worth a look in [](#qc). [](#cell-types) lists the cell types that remain."
    )
    report.metrics(
        [
            sty.Metric("Cells", len(cells)),
            sty.Metric("Pass QC", f"{keep.mean():.0%}", note="genes ≥ 500, MT ≤ 15 %", status="good"),
            sty.Metric("Median genes", float(cells.n_genes.median())),
            sty.Metric("Doublet rate", f"{cells.doublet.mean():.1%}", note="above 3 % expected", status="warning"),
        ]
    )
    with report.section("qc", "Quality control", description="Are the cells intact, and where should the cut-offs go?"):
        report.text(
            "Cells with few detected genes or many mitochondrial reads are usually damaged. "
            "Move the thresholds to see how many cells each choice keeps."
        )
        report.explorer(
            cells,
            controls=[
                sty.Threshold("n_genes", min=500, label="genes per cell"),
                sty.Threshold("pct_mt", max=15, label="% mitochondrial"),
                sty.Select("sample"),
            ],
            views=[
                sty.Stat("{n_pass:,} of {n:,} cells pass ({pct_pass:.1%})"),
                sty.Histogram("n_genes", log_x=True),
                sty.Histogram("pct_mt"),
                sty.Scatter("total_counts", "n_genes", log_x=True, log_y=True),
                sty.Bar("sample"),
            ],
            title="QC thresholds",
            caption="Rows are cells; *passes* means within all thresholds.",
        )
        figure, axes = plt.subplots(1, 2, figsize=(8, 3.2))
        for ax, name in zip(axes, ["donor A", "donor B"], strict=True):
            subset = cells[cells["sample"] == name]
            ax.hist(subset.pct_mt, bins=50, color=sty.palette.categorical(2)[name == "donor B"])
            ax.axvline(15, color="#1d1d1f", linestyle="--", linewidth=1)
            ax.set_title(name)
            ax.set_xlabel("% mitochondrial reads")
        report.figure(
            figure, caption="Mitochondrial fraction per donor; the dashed line is the cut-off.", key="mt-per-donor"
        )
        report.callout(
            "warning",
            "Donor B's mitochondrial fraction is about one point higher across the board ([](#mt-per-donor)), "
            "and so is its gene count ([genes per cell, donor B](#genes-donor-b)).",
        )
        with report.tabs():
            for name in ["donor A", "donor B"]:
                with report.tab(name):
                    figure, ax = plt.subplots(figsize=(7, 2.8))
                    ax.hist(
                        np.log10(cells[cells["sample"] == name].n_genes), bins=60, color=sty.palette.categorical(1)[0]
                    )
                    ax.axvline(np.log10(500), color="#1d1d1f", linestyle="--", linewidth=1)
                    ax.set_xlabel("log10 genes per cell")
                    report.figure(
                        figure, caption=f"Genes per cell, {name}.", key=f"genes-{name.replace(' ', '-').lower()}"
                    )

    with report.section("composition", "Cell type composition"):
        table = (
            cells[keep]
            .groupby(["cell_type", "sample"], observed=True)
            .size()
            .unstack("sample")
            .assign(total=lambda t: t.sum(axis=1))
            .sort_values("total", ascending=False)
        )
        table["share"] = table["total"] / table["total"].sum()
        with report.grid(2):
            report.table(
                table,
                caption="Cells per type after QC.",
                format={"share": "{:.1%}"},
                flags=table["share"] < 0.05,
                key="cell-types",
            )
            figure, ax = plt.subplots(figsize=(4.2, 3.4))
            shares = table["share"].sort_values()
            ax.barh(shares.index, shares.values, color=sty.palette.categorical(1)[0])
            ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
            ax.grid(True, axis="x")
            ax.grid(False, axis="y")
            ax.set_title("Share of cells")
            report.figure(figure, caption="Share of each type.")
        try:
            import plotly.graph_objects as go

            rng = np.random.default_rng(1)
            umap = rng.normal(size=(len(cells), 2)) + cells.cell_type.cat.codes.to_numpy()[:, None] * [2.2, -1.4]
            plot = go.Figure()
            for name in cells.cell_type.cat.categories:
                chosen = (cells.cell_type == name).to_numpy()
                plot.add_scatter(x=umap[chosen, 0], y=umap[chosen, 1], mode="markers", name=name, marker={"size": 4})
            plot.update_layout(title="Embedding (simulated)", xaxis_title="UMAP 1", yaxis_title="UMAP 2", height=460)
            report.plotly(plot, caption="Hover for cell types; click legend entries to hide them.")
        except ImportError:
            report.callout("note", "Install plotly to see the interactive embedding here.")

    with report.section("methods", "Methods"):
        report.text(
            "Cells were filtered on genes per cell and mitochondrial fraction. "
            "Thresholds follow the distribution shapes in [](#qc); doublets were not removed in this demo."
        )
        report.code(
            "import scanpy as sc\nsc.pp.filter_cells(adata, min_genes=500)\nadata = adata[adata.obs.pct_counts_mt < 15]",
            language="python",
            title="Filtering",
            collapsed=True,
        )
    report.provenance()
    return report.save()


if __name__ == "__main__":
    print(build(Path("storyteller-demo.html")))
