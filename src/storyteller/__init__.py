"""storyteller: standalone HTML reports for data analysis, in code flower's style.

    import storyteller as sty

    sty.theme.use()
    report = sty.Report.open("reports/pbmc", title="PBMC cell types")
    with report.section("qc", "Quality control"):
        report.figure(fig, caption="Genes per cell")

See the skill (skill/SKILL.md) for the full workflow.
"""

__version__ = "0.2.1"

from . import theme  # noqa: E402
from .interactive import Bar, Box, Histogram, Scatter, Select, Stat, Table, Threshold, Toggle  # noqa: E402
from .report import Metric, Report  # noqa: E402
from .theme import palette  # noqa: E402

__all__ = [
    "Bar",
    "Box",
    "Histogram",
    "Metric",
    "Report",
    "Scatter",
    "Select",
    "Stat",
    "Table",
    "Threshold",
    "Toggle",
    "__version__",
    "palette",
    "theme",
]
