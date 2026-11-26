import pytest

from storyteller.pdf import launch_options


def test_pdf_uses_the_sandbox_browser_when_there_is_one(tmp_path):
    assert launch_options({}) == {}
    chromium = tmp_path / "chrome-headless-shell"
    chromium.write_text("")
    assert launch_options({"CODEFLOWER_BROWSER_BIN": str(chromium)}) == {
        "executable_path": str(chromium),
        "args": ["--no-sandbox"],
    }


def test_pdf_says_to_install_the_sandbox_browser(tmp_path):
    with pytest.raises(RuntimeError, match="codeflower-browser install"):
        launch_options({"CODEFLOWER_BROWSER_BIN": str(tmp_path / "missing")})
