# Changelog

## 0.1.0

First release.

- Standalone, offline HTML reports: images, charts, data, fonts and styles embedded.
- Live building: `Report.open()` stores the report next to its HTML and saves as it
  changes; keyed sections are replaced when their script re-runs; several scripts can
  fill one report; planned and failed sections show as such.
- Blocks: summary, metrics, text (Markdown), figure, plotly, table, callout, code, grid,
  tabs, provenance, raw HTML; an interactive explorer (thresholds, filters, linked
  histograms, scatter, bars, boxes, tables and live counts) with a static snapshot.
- Image pipeline: SVG for simple matplotlib figures, otherwise 2× rasters; resizing;
  lossless or lossy encoding by content; a size budget that shrinks images to fit.
- code flower's look, with colour-blind-checked categorical, sequential and diverging
  palettes for matplotlib, plotly and the page; light and dark themes.
- Print and PDF: A4 or Letter pages with margins, page numbers and charts sized to the page.
- CLI: outline, build, finalize, remove, check, pdf, serve (live in a browser), palette, demo.
- An agent skill (skill/SKILL.md).
