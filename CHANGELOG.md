# Changelog

## 0.2.2

- The README explains how code flower fits together, and calls the launcher the code flower app.

## 0.2.1

- The README opens with storyteller's icon and links to the other code flower components.

## 0.2.0

- Comments: readers select text, right-click and leave a note, or a request for the agent;
  hovering a highlight shows it with Edit, Resolve and Delete, and a list shows them all.
  Comments are anchored by their words, so they follow the text through rebuilds and live
  updates (and are listed as unanchored when the words go). A stored report keeps them in
  `<name>.report/comments.json` and embeds them in every build. They are saved from VS
  Code's interactive tab, from `storyteller serve`, or, for a file, into a copy the reader
  saves (in this browser until then). `storyteller comments list | reply | resolve |
  import` is how the agent reads and answers them.
- Cross-references: a Markdown link to a block's key, `[](#key)`, becomes a link labelled
  "Figure 3", "Table 1" or "Section 2"; `[text](#key)` keeps its text. Works in text,
  summaries, callouts and captions, across scripts, and as links in the PDF. Figure, table
  and section numbers are now set by Python (they were CSS counters, which skipped hidden
  tabs). `outline` lists linkable keys; `check` and `finalize()` report broken links and
  keys used twice.
- Fixed: Markdown links and headings in text raised a TypeError with markdown-it-py 4, and
  shifted headings closed with the wrong tag.

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
