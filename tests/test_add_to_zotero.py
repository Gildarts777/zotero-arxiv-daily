"""
Tests for the "Add to Zotero" feature — unit tests only, no network required.

Run from the repo root with:
    uv run pytest tests/test_add_to_zotero.py -v
"""
import os
import urllib.parse
import pytest

# ---------------------------------------------------------------------------
# Import from the installed package (works with uv run pytest).
# Set the worker URL *before* the module-level variable is read on first import;
# we also override it directly on the module so tests that import it later are
# consistent regardless of import order.
# ---------------------------------------------------------------------------
os.environ.setdefault("ZOTERO_ADD_URL", "https://zotero-add.example.workers.dev")

import zotero_arxiv_daily.construct_email as ce
from zotero_arxiv_daily.protocol import Paper

# Force the module-level constant to the test URL (in case the module was
# already cached without it).
_TEST_WORKER_URL = "https://zotero-add.example.workers.dev"
ce.ZOTERO_ADD_URL = _TEST_WORKER_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _paper(arxiv_id: str = "2310.12345", tldr: str = "A great finding") -> Paper:
    return Paper(
        source="arxiv",
        title="Attention Is All You Need",
        authors=["Vaswani, Ashish", "Shazeer, Noam"],
        abstract="We propose the Transformer architecture.",
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        tldr=tldr,
        affiliations=["Google Brain"],
        score=8.5,
    )


# ===========================================================================
# _extract_arxiv_id
# ===========================================================================

class TestExtractArxivId:
    def test_standard_url(self):
        assert ce._extract_arxiv_id("https://arxiv.org/abs/2310.12345") == "2310.12345"

    def test_url_with_version(self):
        assert ce._extract_arxiv_id("https://arxiv.org/abs/2310.12345v2") == "2310.12345v2"

    def test_url_with_trailing_slash(self):
        assert ce._extract_arxiv_id("https://arxiv.org/abs/2310.12345/") == "2310.12345"

    def test_non_arxiv_url(self):
        assert ce._extract_arxiv_id("https://example.com/paper") == ""

    def test_empty_string(self):
        assert ce._extract_arxiv_id("") == ""


# ===========================================================================
# _make_add_to_zotero_button
# ===========================================================================

class TestMakeAddToZoteroButton:
    def test_button_contains_worker_url(self):
        html = ce._make_add_to_zotero_button("2310.12345", "Some TLDR")
        assert "zotero-add.example.workers.dev" in html
        assert "/add?" in html

    def test_button_encodes_tldr(self):
        tldr = "This paper introduces a novel method: 2+2=4"
        html = ce._make_add_to_zotero_button("2310.12345", tldr)
        # urlencode uses quote_plus (spaces → "+"); decode before asserting
        assert tldr in urllib.parse.unquote_plus(html)

    def test_button_includes_arxiv_id(self):
        html = ce._make_add_to_zotero_button("2310.12345", "")
        assert "id=2310.12345" in html

    def test_no_button_when_worker_url_empty(self, monkeypatch):
        monkeypatch.setattr(ce, "ZOTERO_ADD_URL", "")
        assert ce._make_add_to_zotero_button("2310.12345", "TLDR") == ""

    def test_no_button_when_arxiv_id_empty(self):
        assert ce._make_add_to_zotero_button("", "TLDR") == ""

    def test_button_label_visible(self):
        html = ce._make_add_to_zotero_button("2310.12345", "")
        assert "Add to Zotero" in html


# ===========================================================================
# get_block_html
# ===========================================================================

class TestGetBlockHtml:
    def test_pdf_button_present(self):
        html = ce.get_block_html(
            title="Test Paper", authors="A, B", rate="7.5",
            tldr="Short summary",
            pdf_url="https://arxiv.org/pdf/2310.12345.pdf",
            affiliations="MIT", arxiv_id="2310.12345",
        )
        assert "PDF" in html
        assert "https://arxiv.org/pdf/2310.12345.pdf" in html

    def test_add_to_zotero_button_present(self):
        html = ce.get_block_html(
            title="Test Paper", authors="A, B", rate="7.5",
            tldr="Short summary",
            pdf_url="https://arxiv.org/pdf/2310.12345.pdf",
            affiliations="MIT", arxiv_id="2310.12345",
        )
        assert "Add to Zotero" in html
        assert "2310.12345" in html

    def test_no_add_button_when_no_id(self):
        html = ce.get_block_html(
            title="Test Paper", authors="A, B", rate="7.5",
            tldr="Short summary",
            pdf_url="https://example.com/paper.pdf",
            affiliations="MIT", arxiv_id="",
        )
        assert "Add to Zotero" not in html


# ===========================================================================
# render_email
# ===========================================================================

class TestRenderEmail:
    def test_full_render_contains_add_button(self):
        html = ce.render_email([_paper()])
        assert "Add to Zotero" in html

    def test_full_render_contains_pdf(self):
        html = ce.render_email([_paper()])
        assert "PDF" in html

    def test_empty_list_shows_rest_message(self):
        html = ce.render_email([])
        assert "Add to Zotero" not in html
        assert "No Papers Today" in html

    def test_tldr_survives_url_encoding(self):
        tldr = "Transformers outperform RNNs on MT tasks"
        html = ce.render_email([_paper(tldr=tldr)])
        # Decode quote_plus encoding (+→space, %3A→:) before asserting
        assert tldr in urllib.parse.unquote_plus(html)

    def test_multiple_papers_all_get_button(self):
        papers = [_paper("2310.00001", "tldr one"), _paper("2310.00002", "tldr two")]
        html = ce.render_email(papers)
        assert html.count("Add to Zotero") == 2
        assert "2310.00001" in html
        assert "2310.00002" in html


# ===========================================================================
# Integration tests — only run when WORKER_URL is set and
# RUN_INTEGRATION_TESTS=1 (never in standard CI).
# ===========================================================================

_SKIP_INTEGRATION = (
    os.environ.get("RUN_INTEGRATION_TESTS", "0") != "1"
    or not os.environ.get("WORKER_URL", "")
)


@pytest.mark.skipif(_SKIP_INTEGRATION, reason="Set RUN_INTEGRATION_TESTS=1 and WORKER_URL to run")
class TestWorkerIntegration:
    """Smoke-tests against the live Cloudflare Worker. Not run in standard CI."""

    @pytest.fixture(autouse=True)
    def worker_url(self):
        self._url = os.environ["WORKER_URL"].rstrip("/")

    def test_add_known_paper(self):
        import urllib.request
        arxiv_id = "1706.03762"
        tldr = "Integration test TLDR — please ignore"
        url = (
            f"{self._url}/add"
            f"?id={urllib.parse.quote(arxiv_id)}"
            f"&tldr={urllib.parse.quote(tldr)}"
        )
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ZoteroTest/1.0)"}), timeout=30) as resp:
            assert resp.status == 200
            body = resp.read().decode()
        assert "Zotero" in body
        assert "Errore" not in body

    def test_missing_id_returns_400(self):
        import urllib.request, urllib.error
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(urllib.request.Request(f"{self._url}/add", headers={"User-Agent": "Mozilla/5.0 (compatible; ZoteroTest/1.0)"}), timeout=10)
        assert exc_info.value.code == 400

    def test_invalid_id_returns_html(self):
        import urllib.request
        url = f"{self._url}/add?id=1512.03385"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ZoteroTest/1.0)"}), timeout=30) as resp:
            body = resp.read().decode()
        assert "<!DOCTYPE html>" in body
