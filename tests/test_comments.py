import json
import os
import time

import pytest

import storyteller as sty
from storyteller import comments
from storyteller.cli import main


def _comment(id="c-aaaa1111", kind="request", body="Say kept, not pass.", exact="6,000 cells pass QC", **extra):
    return {
        "id": id,
        "kind": kind,
        "status": "open",
        "body": body,
        "anchor": {"part": "section:qc", "near": "s-qc", "exact": exact, "prefix": "", "suffix": "", "start": 0},
        "created": "2026-10-29T10:00:00Z",
        **extra,
    }


def _stored_report(tmp_path, *items):
    report = sty.Report.open(tmp_path / "pbmc", title="PBMC")
    with report.section("qc", "Quality control"):
        report.text("Of 7,000 barcodes, **6,000 cells pass QC**.")
    report.save()
    if items:
        comments.save(tmp_path / "pbmc", list(items))
    return report


def test_comments_are_embedded_safely(tmp_path):
    attack = "</script><script>alert(1)</script><!--"
    report = sty.Report("x", tmp_path / "r.html")
    html = report.render(comments=[_comment(body=attack)])
    assert "<script>alert(1)" not in html
    assert comments.embedded(html)[0]["body"] == attack


def test_only_well_formed_comments_are_kept():
    cleaned = comments.clean(
        [
            _comment(extra="dropped", replies="not a list", kind="weird", status="?"),
            {"id": 3, "body": "no string id"},
            "not a comment",
        ]
    )
    assert len(cleaned) == 1
    assert "extra" not in cleaned[0] and "replies" not in cleaned[0]
    assert (cleaned[0]["kind"], cleaned[0]["status"]) == ("note", "open")
    with pytest.raises(ValueError):
        comments.clean({"not": "a list"})


def test_a_stored_report_keeps_its_comments_across_builds(tmp_path):
    report = _stored_report(tmp_path, _comment())
    assert json.loads((tmp_path / "pbmc.report" / "comments.json").read_text())[0]["id"] == "c-aaaa1111"
    assert comments.embedded((tmp_path / "pbmc.html").read_text())[0]["id"] == "c-aaaa1111"
    # The analysis carries on: the page is rebuilt with the comments in it.
    with report.section("qc", "Quality control"):
        report.text("Of 7,000 barcodes, **6,000 cells pass QC** (rewritten).")
    report.save()
    assert comments.embedded((tmp_path / "pbmc.html").read_text())[0]["id"] == "c-aaaa1111"


def test_a_build_adopts_comments_saved_over_the_page(tmp_path):
    report = _stored_report(tmp_path, _comment())
    # The reader saved the page from a browser, over the file, with another comment.
    html_path = tmp_path / "pbmc.html"
    html_path.write_text(comments.embed(html_path.read_text(), [_comment(), _comment(id="c-bbbb2222")]))
    later = time.time() + 5
    os.utime(html_path, (later, later))
    report.save()
    ids = [c["id"] for c in comments.load(tmp_path / "pbmc")]
    assert ids == ["c-aaaa1111", "c-bbbb2222"]
    assert [c["id"] for c in comments.embedded(html_path.read_text())] == ids


def test_a_saved_html_file_keeps_its_comments_when_written_again(tmp_path):
    report = sty.Report("x", tmp_path / "r.html")
    report.text("hello")
    report.save()
    comments.save(tmp_path / "r.html", [_comment()])
    report.save()
    assert comments.load(tmp_path / "r.html")[0]["id"] == "c-aaaa1111"


def test_agent_lists_replies_and_resolves(tmp_path, capsys):
    _stored_report(tmp_path, _comment(), _comment(id="c-note0000", kind="note", body="Nice."))
    assert main(["comments", "list", str(tmp_path / "pbmc")]) == 0
    listed = capsys.readouterr().out
    assert "c-aaaa1111  request for you, open  in section:qc (near #s-qc)" in listed
    assert 'on: "6,000 cells pass QC"' in listed and "> Say kept, not pass." in listed

    assert main(["comments", "reply", str(tmp_path / "pbmc.html"), "c-aaaa1111", "Which wording?"]) == 0
    assert main(["comments", "resolve", str(tmp_path / "pbmc"), "c-aaaa1111", "Changed to kept."]) == 0
    capsys.readouterr()
    stored = comments.load(tmp_path / "pbmc")[0]
    assert stored["status"] == "resolved"
    assert [r["body"] for r in stored["replies"]] == ["Which wording?", "Changed to kept."]
    # The page has the answer too, without a rebuild.
    assert comments.embedded((tmp_path / "pbmc.html").read_text())[0]["status"] == "resolved"

    assert main(["comments", "list", str(tmp_path / "pbmc")]) == 0
    assert "c-aaaa1111" not in capsys.readouterr().out
    assert main(["comments", "list", str(tmp_path / "pbmc"), "--all", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert main(["comments", "resolve", str(tmp_path / "pbmc"), "c-missing"]) == 1


def test_comments_from_a_saved_copy_are_imported(tmp_path, capsys):
    _stored_report(tmp_path, _comment(updated="2026-10-29T10:00:00Z"))
    copy = tmp_path / "copy.html"
    edited = _comment(body="Edited later.", updated="2026-10-30T09:00:00Z")
    copy.write_text(comments.embed((tmp_path / "pbmc.html").read_text(), [edited, _comment(id="c-new00000")]))
    assert main(["comments", "import", str(tmp_path / "pbmc"), str(copy)]) == 0
    assert capsys.readouterr().out.strip() == "1 added, 1 updated"
    assert [c["body"] for c in comments.load(tmp_path / "pbmc")] == ["Edited later.", "Say kept, not pass."]
