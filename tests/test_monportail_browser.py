import pytest

playwright = pytest.importorskip("playwright.sync_api")
pypdf = pytest.importorskip("pypdf")

from monportail.inventory import VideoAwareEna  # noqa: E402

WIDE_PAGE = "<div style='min-width:1300px'><table style='width:100%'><tr><td>a</td><td style='text-align:right'>10,5 / 15</td></tr></table></div>"


class FakeSession:
    def __init__(self, context, page):
        self.contexte, self.page = context, page


def test_pdf_keeps_the_full_page_width(tmp_path):
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium is not installed")
        context = browser.new_context()
        page = context.new_page()
        page.set_content(WIDE_PAGE)
        ena = VideoAwareEna(FakeSession(context, page))
        ena._assurer_authentifie = lambda: None
        target = tmp_path / "page.pdf"
        ena.capturer_pdf_page_courante(target)
        browser.close()
    width = float(pypdf.PdfReader(target).pages[0].mediabox.width)
    # 1300 CSS px = 975 pt, wider than A4 (595 pt): the right column is not cut off.
    assert width >= 975
