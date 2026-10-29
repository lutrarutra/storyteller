import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

import storyteller as sty
from storyteller import check
from storyteller.interactive import explorer as ex
from storyteller.interactive import pack


def _sections(html: str) -> list[str]:
    return re.findall(r'<section class="cf-section[^"]*" id="s-([^"]+)"', html)


def test_in_memory_report_is_self_contained_and_final(tmp_path):
    report = sty.Report("Test <report>", tmp_path / "r.html", subtitle="sub")
    report.summary("Some **findings**.")
    with report.section("Data"):
        report.text("A paragraph with `code`.")
        report.table({"a": [1, 2], "b": [0.5, 0.25]})
    path = report.save()
    html = path.read_text()
    assert check.check_html(html) == []
    assert "Test &lt;report&gt;" in html
    assert 'class="cf-pill cf-pill-final"' in html and ">Draft" not in html
    assert _sections(html) == ["data"]


def test_text_and_captions_are_escaped(tmp_path):
    report = sty.Report("x", tmp_path / "r.html")
    attack = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    with report.section("s", "Section " + attack):
        report.text(attack)
        report.table({"col": [attack]})
        report.figure(np.zeros((4, 4)), caption=attack)
        report.metrics({attack: attack})
    html = report.render()
    body = html.split("<body>", 1)[1]
    # The only scripts are the theme JSON and the runtime.
    assert len(re.findall(r"<script", body)) == 2
    assert "onerror=alert" not in body.replace("onerror=alert(2)&gt;", "")


def test_raw_html_only_on_request(tmp_path):
    report = sty.Report("x", tmp_path / "r.html")
    report.text("<b>bold</b>", key="t")
    assert "&lt;b&gt;" in report.render()
    report.html("<b>bold</b>", key="h")
    assert "<b>bold</b>" in report.render()


def test_same_input_renders_identical_bytes(tmp_path):
    def build():
        report = sty.Report("Same", date="2026-01-01")
        with report.section("one", "One"):
            report.text("hello")
            report.figure(np.eye(8))
        return report.render()

    assert build() == build()


def test_markdown_images_are_embedded(tmp_path):
    from PIL import Image

    (tmp_path / "figures").mkdir()
    Image.new("RGB", (10, 10), "red").save(tmp_path / "figures" / "a.png")
    report = sty.Report("x", tmp_path / "r.html", base_dir=tmp_path)
    report.text("![A](figures/a.png) and ![missing](figures/nope.png)", key="t")
    html = report.render()
    assert 'alt="A"' in html and "data:image/" in html
    problems = check.check_html(html)
    assert any("nope.png" in p for p in problems)


def test_check_flags_external_resources():
    html = (
        '<link rel="stylesheet" href="https://cdn/x.css"><script src="https://cdn/plotly.js"></script>'
        '<img src="figures/a.png"><style>body{background:url(bg.png)}</style>'
        '<a href="https://example.org">fine</a><img src="data:image/png;base64,AAAA">'
    )
    problems = check.check_html(html)
    assert len(problems) == 4


def test_live_store_reruns_replace_sections(tmp_path):
    path = tmp_path / "reports" / "pbmc"
    report = sty.Report.open(path, title="Live")
    report.pending("cluster", "Clustering", note="later")
    with report.section("qc", "Quality control"):
        report.text("first run")
        report.text("stale block")
    report.close()
    document = json.loads((tmp_path / "reports" / "pbmc.report" / "report.json").read_text())
    assert [n["key"] for n in document["nodes"]] == ["cluster", "qc"]

    again = sty.Report.open(path)
    with again.section("qc", "Quality control"):
        again.text("second run")
    again.close()
    html = (tmp_path / "reports" / "pbmc.html").read_text()
    assert "second run" in html and "first run" not in html and "stale block" not in html
    assert html.count('id="s-qc"') == 1
    assert 'class="cf-pill cf-pill-draft"' in html and "planned" in html


def test_pending_does_not_wipe_a_written_section(tmp_path):
    report = sty.Report.open(tmp_path / "r", title="x")
    with report.section("qc", "QC"):
        report.text("content")
    report.pending("qc", "QC", note="should not replace")
    assert "content" in report.render()


def test_failed_section_shows_the_error(tmp_path):
    report = sty.Report.open(tmp_path / "r", title="x")
    with pytest.raises(ValueError), report.section("qc", "QC"):
        report.text("partial")
        raise ValueError("bad input file")
    html = (tmp_path / "r.html").read_text()
    assert "This section failed to update" in html and "bad input file" in html


def test_two_scripts_fill_one_report(tmp_path):
    """Separate processes write separate sections; neither loses the other's work."""
    script = textwrap.dedent(
        """
        import sys, storyteller as sty
        key = sys.argv[1]
        report = sty.Report.open("reports/shared", title="Shared")
        with report.section(key, key.upper()):
            report.text("written by " + key)
        """
    )
    (tmp_path / "write.py").write_text(script)
    processes = [
        subprocess.Popen([sys.executable, "write.py", key], cwd=tmp_path) for key in ("alpha", "beta", "gamma")
    ]
    assert all(p.wait(timeout=60) == 0 for p in processes)
    html = (tmp_path / "reports" / "shared.html").read_text()
    assert sorted(_sections(html)) == ["alpha", "beta", "gamma"]
    assert "from <code>write.py</code>".lower() in html.lower()


def test_autosave_is_debounced(tmp_path, monkeypatch):
    report = sty.Report.open(tmp_path / "r", title="x")
    saves = []
    original = report.save
    monkeypatch.setattr(report, "save", lambda *a, **k: saves.append(1) or original(*a, **k))
    for i in range(30):
        report.text(f"line {i}", key=f"t{i}")
    assert len(saves) <= 2
    report.close()
    assert "line 29" in (tmp_path / "r.html").read_text()


def test_html_is_replaced_atomically(tmp_path):
    report = sty.Report.open(tmp_path / "r", title="x")
    report.save()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_finalize_drops_planned_sections_and_draft_marks(tmp_path, capsys):
    report = sty.Report.open(tmp_path / "r", title="x")
    report.pending("later", "Later")
    with report.section("now", "Now"):
        report.text("done")
    path = report.finalize()
    html = path.read_text()
    assert _sections(html) == ["now"]
    assert 'class="cf-pill cf-pill-final"' in html and 'class="cf-pill cf-pill-draft"' not in html
    assert "Provenance" in html
    assert "planned but never written" in capsys.readouterr().err
    # New work turns it back into a draft.
    with report.section("more", "More"):
        report.text("x")
    assert 'class="cf-pill cf-pill-draft"' in (tmp_path / "r.html").read_text()


def test_size_budget_shrinks_images(tmp_path):
    rng = np.random.default_rng(0)
    report = sty.Report("big", tmp_path / "big.html", max_bytes=1_200_000, fonts=False)
    with report.section("s", "Images"):
        for i in range(3):
            report.figure(rng.integers(0, 255, (900, 900, 3), dtype=np.uint8), key=f"f{i}")
    before = len(report.render().encode())
    messages = report.fit_budget()
    after = len(report.render().encode())
    assert before > 1_200_000 and after <= 1_200_000
    assert messages and "shrank" in messages[0]


def test_outline_lists_keys(tmp_path):
    report = sty.Report.open(tmp_path / "r", title="Outline")
    report.pending("b", "B")
    with report.section("a", "A"):
        report.text("x")
    text = report.outline()
    assert "[b] B  (pending" in text and "[a] A  (ready, 1 blocks)" in text


def test_pack_round_trip(cells):
    blob, columns, n = pack.pack(cells, ["n_genes", "sample", "doublet"])
    values = pack.unpack(blob, columns)
    assert n == len(cells)
    np.testing.assert_allclose(values["n_genes"], cells.n_genes.to_numpy(), rtol=1e-6)
    assert list(values["sample"][:5]) == list(cells["sample"].astype(str)[:5])
    assert values["doublet"].dtype == bool


def test_pack_handles_strings_and_missing():
    data = {"label": ["a", None, "b", "a"], "x": [1.0, np.nan, 3.0, 4.0]}
    blob, columns, _ = pack.pack(data, ["label", "x"])
    values = pack.unpack(blob, columns)
    assert list(values["label"]) == ["a", None, "b", "a"]
    assert np.isnan(values["x"][1])


def test_explorer_masks_and_stats(cells):
    controls = [sty.Threshold("n_genes", min=500), sty.Threshold("pct_mt", max=15), sty.Select("sample", "donor A")]
    blob, columns, _ = pack.pack(cells, ["n_genes", "pct_mt", "sample"])
    values = pack.unpack(blob, columns)
    subset, passes = ex.masks(values, controls)
    expected_subset = (cells["sample"] == "donor A").to_numpy()
    expected_pass = ((cells.n_genes >= 500) & (cells.pct_mt <= 15)).to_numpy()
    assert (subset == expected_subset).all() and (passes == expected_pass).all()
    stats = ex.stats(subset, passes)
    assert stats["n"] == expected_subset.sum()
    assert ex.format_template("{n_pass:,} of {n:,} ({pct_pass:.1%})", stats).endswith("%)")


def test_stat_template_rejects_unknown_fields():
    with pytest.raises(ValueError, match="n_pass"):
        ex.check_template("{cells} pass")
    with pytest.raises(ValueError):
        ex.check_template("{n:>10}")


def test_explorer_block_renders_static_and_live(tmp_path, cells):
    report = sty.Report("x", tmp_path / "e.html")
    with report.section("qc", "QC"):
        report.explorer(
            cells,
            [sty.Threshold("n_genes", min=500), sty.Select("sample")],
            [sty.Stat("{n_pass} pass"), sty.Histogram("n_genes"), sty.Table(["n_genes", "sample"])],
            title="QC",
        )
    html = report.render()
    assert 'class="cf-static"' in html and 'class="cf-live"' in html
    assert "plotly-cartesian" in html
    assert "Settings: n_genes ≥ 500 · sample: all" in html
    spec = json.loads(
        re.search(r'<script type="application/json" id="cf-explorer-[^"]+">(.*?)</script>', html).group(1)
    )
    assert spec["n"] == len(cells) and spec["columns"][0]["min"] < spec["columns"][0]["max"]


def test_explorer_type_errors(cells):
    report = sty.Report("x")
    with pytest.raises(TypeError, match="numeric"):
        report.explorer(cells, [sty.Threshold("sample", min=1)], [sty.Histogram("n_genes")])
    with pytest.raises(KeyError, match="nope"):
        report.explorer(cells, [], [sty.Histogram("nope")])


def test_explorer_samples_large_data(cells):
    report = sty.Report("x")
    report.explorer(
        cells, [sty.Threshold("n_genes", min=500)], [sty.Histogram("n_genes")], max_rows=100, static=False, key="e"
    )
    node = report._doc["nodes"][0]
    assert node["spec"]["n"] == 100
    assert "random sample of 100 of 800" in node["caption_html"]


def test_plotly_picks_the_smallest_bundle(tmp_path):
    import plotly.graph_objects as go

    report = sty.Report("x")
    report.plotly(go.Figure(go.Bar(x=[1, 2], y=[3, 4])), key="bar")
    assert 'data-cf-lib="plotly-cartesian"' in report.render()
    report.plotly(go.Figure(go.Sankey()), key="sankey")
    html = report.render()
    assert 'data-cf-lib="plotly-strict"' in html and 'data-cf-lib="plotly-cartesian"' not in html


def test_no_plotly_library_without_interactive_blocks():
    report = sty.Report("x")
    report.text("just text", key="t")
    assert "<script data-cf-lib=" not in report.render()


def test_cli_outline_and_build(tmp_path):
    report = sty.Report.open(tmp_path / "r", title="CLI")
    with report.section("a", "A"):
        report.text("x")
    result = subprocess.run(
        [sys.executable, "-m", "storyteller.cli", "outline", str(tmp_path / "r")], capture_output=True, text=True
    )
    assert result.returncode == 0 and "[a] A" in result.stdout
    result = subprocess.run(
        [sys.executable, "-m", "storyteller.cli", "check", str(tmp_path / "r.html")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and "self-contained" in result.stdout


def test_demo_builds(tmp_path):
    from storyteller.demo import build

    path = build(Path(tmp_path / "demo.html"))
    assert check.check_file(path) == []
    assert path.stat().st_size < 9_500_000
