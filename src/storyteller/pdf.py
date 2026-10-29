"""PDF export through headless Chromium (playwright), which runs the report's charts before printing.

Without playwright, open the report in a browser and use its "Print / PDF" button:
the print styles give the same result.
"""

from __future__ import annotations

from pathlib import Path

INSTALL_HINT = (
    "PDF export needs playwright with Chromium: `uv pip install playwright && playwright install chromium`. "
    "Without it, open the report in a browser and use Print / PDF."
)


def to_pdf(html_path: str | Path, pdf_path: str | Path | None = None, *, page: str | None = None) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(INSTALL_HINT) from error
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path) if pdf_path else html_path.with_suffix(".pdf")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # browser binaries missing
            raise RuntimeError(f"{error}\n{INSTALL_HINT}") from error
        try:
            context = browser.new_context(offline=True)
            tab = context.new_page()
            tab.goto(html_path.as_uri(), wait_until="load")
            # Explorers decode their data asynchronously; wait for them to draw.
            tab.wait_for_function(
                "[...document.querySelectorAll('[data-cf-explorer]')].every(e => e.classList.contains('cf-ready'))",
                timeout=30_000,
            )
            tab.emulate_media(media="print")
            tab.evaluate("window.dispatchEvent(new Event('beforeprint'))")
            tab.wait_for_timeout(300)
            options = {"path": str(pdf_path), "print_background": True, "prefer_css_page_size": True}
            if page:
                options["format"] = page
            tab.pdf(**options)
        finally:
            browser.close()
    return pdf_path
