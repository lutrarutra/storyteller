"""Lays out the report before the analysis runs: the user sees its plan at once.

Run the scripts in order from this folder (python 00_outline.py; python 01_qc.py; ...)
and keep reports/pbmc.html open (codeflower-show, or storyteller serve reports/pbmc).
"""

import storyteller as sty

report = sty.Report.open("reports/pbmc", title="PBMC quality control", subtitle="Simulated data, two donors")
report.pending("qc", "Quality control", note="Filtering damaged cells")
report.pending("composition", "Cell type composition")
