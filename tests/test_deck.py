import io
import json
import re
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("pptx")

import storyteller as sty  # noqa: E402
from storyteller import cli  # noqa: E402
from storyteller.deck import build, office  # noqa: E402
from storyteller.deck import template as templates  # noqa: E402
from storyteller.deck.deck import draw  # noqa: E402


@pytest.fixture(autouse=True)
def _no_drawing(monkeypatch):
    # LibreOffice is slow and not everywhere: tests draw only when they ask to.
    monkeypatch.setenv("STORYTELLER_DRAW", "0")


def read_pptx(path="decks/d.pptx"):
    from pptx import Presentation

    return Presentation(path)


def texts(slide):
    return [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame and shape.text_frame.text]


def small_figure():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([1, 2, 3], [1, 4, 9])
    return fig


def test_open_writes_the_pptx_the_viewer_and_the_store():
    d = sty.Deck.open("decks/d", title="A deck")
    with d.slide("intro", "A deck", subtitle="The subtitle"):
        d.notes("Say hello.")
    d.close()
    assert Path("decks/d.pptx").is_file()
    assert Path("decks/d.report/deck.json").is_file()
    assert Path("decks/d.report/template.pptx").is_file()
    html = Path("decks/d.html").read_text()
    assert '<meta name="generator" content="storyteller ' in html
    assert '<meta name="cf-deck" content="d.pptx">' in html
    assert 'data-cf-part="slide:intro"' in html
    prs = read_pptx()
    assert len(prs.slides) == 1
    assert texts(prs.slides[0]) == ["A deck", "The subtitle"]
    assert prs.slides[0].notes_slide.notes_text_frame.text == "Say hello."
    assert prs.slide_width == round(13.333 * templates.EMU_PER_INCH)


def test_a_rerun_replaces_the_slide_in_place():
    d = sty.Deck.open("decks/d", title="A deck")
    for title in ("First", "Second"):
        with d.slide("a", title):
            d.text("- one")
        with d.slide("b", "B"):
            d.text("- two")
    d.close()
    prs = read_pptx()
    assert [texts(s)[0] for s in prs.slides] == ["Second", "B"]


def test_a_section_has_a_header_slide_and_is_replaced_as_a_whole():
    d = sty.Deck.open("decks/d", title="A deck")
    with d.section("qc", "Quality control", description="Are the cells intact?"):
        with d.slide("x", "X"):
            d.text("x")
        with d.slide("y", "Y"):
            d.text("y")
    with d.section("qc", "Quality control", description="Are the cells intact?"), d.slide("z", "Z"):
        d.text("z")
    d.close()
    titles = [texts(s)[0] for s in read_pptx().slides]
    assert titles == ["Quality control", "Z"]
    assert "Are the cells intact?" in texts(read_pptx().slides[0])
    outline = sty.Deck.open("decks/d").outline()
    assert "[qc] Quality control" in outline and "§qc  section header" in outline and "#z  content" in outline


def test_two_scripts_fill_one_deck():
    first = sty.Deck.open("decks/d", title="A deck")
    with first.slide("a", "A"):
        first.text("a")
    first.close()
    second = sty.Deck.open("decks/d")
    with second.slide("b", "B"):
        second.text("b")
    second.close()
    assert [texts(s)[0] for s in read_pptx().slides] == ["A", "B"]


def test_layouts_follow_the_blocks():
    d = sty.Deck.open("decks/d", title="A deck")
    with d.slide("cover", "Cover"):
        pass
    with d.slide("one", "One block"):
        d.text("- a")
    with d.slide("two", "Two blocks"):
        d.text("- a")
        d.figure(small_figure())
    with d.slide("said", "A statement"):
        pass
    with d.slide("explicit", "Explicit", layout="statement"):
        pass
    d.close()
    built = json.loads(Path("decks/d.report/build.json").read_text())
    assert [s["layout"] for s in built["slides"]] == ["title", "content", "two", "statement", "statement"]
    with pytest.raises(ValueError, match="unknown slide layout 'grid'"), d.slide("bad", layout="grid"):
        pass


def test_blocks_fill_regions_and_empty_placeholders_go():
    d = sty.Deck.open("decks/d", title="A deck")
    with d.slide("mix", "Mixed"):
        d.text("- **bold** point\n- second\n  - nested")
        d.figure(small_figure(), caption="A caption.")
    with d.slide("table", "Table"):
        d.table({"name": ["a", "b"], "n": [1200, 3]}, caption="Counts.")
    with d.slide("metrics", "Metrics"):
        d.metrics([sty.Metric("Cells", 9871, note="kept"), sty.Metric("Doublets", "3%", status="warning")])
    d.close()
    mix, table, metrics = read_pptx().slides
    body = next(s for s in mix.shapes if s.is_placeholder and s.placeholder_format.idx == 1)
    paragraphs = body.text_frame.paragraphs
    assert [p.text for p in paragraphs] == ["bold point", "second", "nested"]
    assert paragraphs[0].runs[0].font.bold and paragraphs[2].level == 1
    assert any(shape.shape_type == 13 for shape in mix.shapes)  # a picture
    assert "A caption." in texts(mix)
    grid = next(shape for shape in table.shapes if shape.has_table).table
    assert [grid.cell(0, c).text for c in range(2)] == ["name", "n"]
    assert grid.cell(1, 1).text == "1,200"
    assert "9,871\nCells\nkept" in texts(metrics)
    for slide in (mix, table, metrics):
        for shape in slide.placeholders:
            assert shape.text_frame.text.strip(), "an empty placeholder was left on the slide"


def test_slide_rules_are_explained():
    d = sty.Deck.open("decks/d", title="A deck")
    with pytest.raises(RuntimeError, match="inside `with deck.slide"):
        d.text("loose")
    with d.slide("t", "Table"), pytest.raises(ValueError, match="at most 12 rows"):
        d.table({"n": list(range(20))})
    with pytest.raises(RuntimeError, match="slides do not nest"), d.slide("outer"), d.slide("inner"):
        pass


def test_a_failing_slide_shows_its_error():
    d = sty.Deck.open("decks/d", title="A deck")
    with pytest.raises(ZeroDivisionError), d.slide("bad", "Bad"):
        d.text("before")
        1 / 0  # noqa: B018
    d.close()
    assert any("This slide failed to update" in text for text in texts(read_pptx().slides[0]))
    assert "This slide failed to update" in Path("decks/d.html").read_text()


def test_pending_sections_show_in_drafts_only():
    d = sty.Deck.open("decks/d", title="A deck")
    d.pending("later", "Later", note="Comes later")
    with d.slide("a", "A"):
        d.text("a")
    d.close()
    assert len(read_pptx().slides) == 2
    d._set_meta(final=True)
    d.save()
    assert len(read_pptx().slides) == 1


def test_viewer_shows_outlines_until_drawn_and_names_missing_libreoffice(monkeypatch):
    monkeypatch.delenv("STORYTELLER_SOFFICE", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(office, "MAC_SOFFICE", "/nonexistent")
    d = sty.Deck.open("decks/d", title="A deck")
    with d.section("s", "Section"), d.slide("a", "Point A"):
        d.text("- alpha")
    d.close()
    html = Path("decks/d.html").read_text()
    assert 'data-cf-slot="section:s"' in html and 'data-cf-part="head:s"' in html
    assert "cf-slide-wire" in html and "Drawing…" in html and "alpha" in html
    drawn = draw(Path("decks/d.report"), Path("decks/d.html"))
    assert "LibreOffice is not installed" in drawn["error"]
    assert "Slides not drawn: LibreOffice is not installed" in Path("decks/d.html").read_text()


def test_markdown_becomes_paragraphs_and_runs():
    items = build.paragraphs("Some *it* and `code`\n\n1. first\n2. second\n   - inner")
    assert [(p.level, p.bullet) for p in items] == [(0, None), (0, "number"), (0, "number"), (1, "bullet")]
    assert [(r.text, r.italic, r.code) for r in items[0].runs] == [
        ("Some ", False, False),
        ("it", True, False),
        (" and ", False, False),
        ("code", False, True),
    ]


def test_default_template_has_the_standard_layouts_and_theme():
    from pptx import Presentation

    prs = Presentation(io.BytesIO(templates.default_template()))
    names = {layout.name for layout in prs.slide_layouts}
    assert {"Title Slide", "Title and Content", "Two Content", "Section Header", "Title Only", "Blank"} <= names
    theme = zipfile.ZipFile(io.BytesIO(templates.default_template())).read("ppt/theme/theme1.xml").decode()
    assert 'name="code flower"' in theme and '<a:latin typeface="Arial"/>' in theme
    assert re.search(r'<a:accent1><a:srgbClr val="4F46E5"/>', theme)


def test_a_potx_template_and_layout_overrides(tmp_path):
    potx = tmp_path / "brand.potx"
    data = templates.default_template()
    source = zipfile.ZipFile(io.BytesIO(data))
    with zipfile.ZipFile(potx, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                content = content.replace(templates.PPTX_TYPE, templates.POTX_TYPE)
            target.writestr(item, content)
    d = sty.Deck.open("decks/d", title="A deck", template=potx, layouts={"content": "Title Only"})
    with d.slide("a", "A"):
        d.text("- point")
    d.close()
    assert json.loads(Path("decks/d.report/deck.json").read_text())["meta"]["template"] == "brand"
    slide = read_pptx().slides[0]
    assert slide.slide_layout.name == "Title Only"
    assert "point" in texts(slide)  # no region in the layout: a text box in the content area
    with pytest.raises(ValueError, match="no layout 'Nope'"):
        sty.Deck.open("decks/e", title="E", layouts={"content": "Nope"})


def test_cli_works_on_decks(capsys):
    d = sty.Deck.open("decks/d", title="A deck")
    with d.slide("a", "A"):
        d.text("a")
    d.close()
    assert cli.main(["outline", "decks/d"]) == 0
    assert "#a  content" in capsys.readouterr().out
    assert cli.main(["remove", "decks/d", "a"]) == 0
    assert len(read_pptx().slides) == 0


def test_reports_and_decks_do_not_share_a_store():
    sty.Report.open("same", title="Report").close()
    with pytest.raises(ValueError, match="belongs to a report"):
        sty.Deck.open("same", title="Deck")


@pytest.mark.skipif(office.soffice() is None, reason="LibreOffice is not installed")
def test_libreoffice_draws_every_slide(monkeypatch):
    monkeypatch.delenv("STORYTELLER_DRAW")
    d = sty.Deck.open("decks/d", title="A deck")
    with d.slide("a", "A"):
        d.text("- one")
    with d.slide("b", "B"):
        d.figure(small_figure())
    drawn = d.wait()
    d.close()
    drawn = d.wait()
    assert drawn.get("error") is None
    assert [Path("decks/d.report/render", s["png"]).is_file() for s in drawn["slides"]] == [True, True]
    assert Path("decks/d.html").read_text().count('class="cf-slide-image"') == 2


def test_template_fonts_are_found_in_the_decks_fonts_folder(tmp_path):
    from PIL import ImageFont

    from storyteller.deck import fonts

    source = Path(ImageFont.truetype("DejaVuSans.ttf", 12).path) if hasattr(ImageFont, "truetype") else None
    if source is None or not source.is_file():
        pytest.skip("no DejaVu Sans to stand in for a template font")
    folder = tmp_path / "fonts"
    folder.mkdir()
    (folder / source.name).write_bytes(source.read_bytes())
    assert fonts.files("DejaVu Sans Book", [folder])  # a style word in the family name is dropped
    assert fonts.files("No Such Font", [folder]) == []
    width = fonts.line_width("Hello", "DejaVu Sans", 20, False, [folder])
    assert width and 40 < width < 80


def test_a_template_with_missing_fonts_is_reported(monkeypatch):
    from storyteller.deck import fonts

    def with_theme_font(family: str) -> bytes:
        source = zipfile.ZipFile(io.BytesIO(templates.default_template()))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as target:
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename.startswith("ppt/theme/"):
                    content = content.replace(b'typeface="Arial"', f'typeface="{family}"'.encode())
                target.writestr(item, content)
        return out.getvalue()

    monkeypatch.setattr(fonts, "_system", lambda: {"liberation sans": [Path("/fonts/LiberationSans.ttf")]})
    assert fonts.missing(with_theme_font("Nowhere Grotesk")) == ["Nowhere Grotesk"]
    assert fonts.missing(templates.default_template()) == []  # Arial: LibreOffice has Liberation Sans
    monkeypatch.setattr(fonts, "_system", lambda: {})
    assert fonts.missing(templates.default_template()) == ["Arial"]


def test_finalize_writes_the_final_deck_and_names_what_is_unfinished(capsys):
    d = sty.Deck.open("decks/d", title="A deck")
    d.pending("later", "Later")
    with d.slide("a", "A"):  # a top-level slide whose blocks have no keys
        d.text("- one")
        d.figure(small_figure())
    d.finalize()
    err = capsys.readouterr().err
    assert "section 'later' was planned but never written" in err
    assert len(read_pptx().slides) == 1
    assert json.loads(Path("decks/d.report/deck.json").read_text())["meta"]["final"] is True


def test_the_viewer_offers_the_deck_for_download():
    d = sty.Deck.open("decks/d", title="A deck")
    with d.slide("a", "A"):
        d.text("a")
    d.close()
    html = Path("decks/d.html").read_text()
    assert 'data-cf-action="download" data-cf-file="d.pptx"' in html
    assert '<a href="d.pptx">' not in html  # no link in the bar: in a host it would take the frame away
    assert "@page{size:13.333in 7.500in;margin:0" in html


def test_the_default_template_has_dark_twins_and_the_brand_line():
    from pptx import Presentation

    prs = Presentation(io.BytesIO(templates.default_template()))
    names = {layout.name for layout in prs.slide_layouts}
    assert {"Title Slide (dark)", "Title and Content (dark)", "Two Content (dark)"} <= names
    assert templates.choose(prs, "content", mode="dark").name == "Title and Content (dark)"
    assert templates.choose(prs, "content").name == "Title and Content"
    master = prs.slide_master._element
    assert templates.BRAND_LINE in "".join(master.itertext())
    assert any(shape.shape_type == 13 for shape in prs.slide_master.shapes)  # the logo


def test_a_dark_deck_and_a_light_slide_in_it():
    d = sty.Deck.open("decks/d", title="A deck", mode="dark")
    with d.slide("a", "A"):
        d.text("a")
    with d.slide("b", "B", mode="light"):
        d.text("b")
    d.close()
    first, second = read_pptx().slides
    assert first.slide_layout.name == "Title and Content (dark)"
    assert second.slide_layout.name == "Title and Content"
    with pytest.raises(ValueError, match="mode is 'light' or 'dark'"):
        sty.Deck.open("decks/d", mode="dim")


def test_back_to_the_default_template_by_name(tmp_path):
    potx = tmp_path / "brand.pptx"
    potx.write_bytes(templates.default_template())
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "x.ttf").write_bytes(b"not really a font")
    sty.Deck.open("decks/d", title="A deck", template=potx).close()
    assert Path("decks/d.report/fonts/x.ttf").is_file()
    sty.Deck.open("decks/d", template="codeflower").close()
    assert json.loads(Path("decks/d.report/deck.json").read_text())["meta"]["template"] == "code flower"
    assert not Path("decks/d.report/fonts").exists()
