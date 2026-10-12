"""
Tests for the "Add to Zotero" feature.

Run from the repo root with:
    uv run pytest tests/test_add_to_zotero.py -v

Or install deps and run:
    pip install pytest
    pytest tests/test_add_to_zotero.py -v
"""
import os
import sys
import types
import urllib.parse
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal stubs so we can import construct_email without the full package
# ---------------------------------------------------------------------------

def _make_protocol_stub():
    """Create a minimal stub for src/zotero_arxiv_daily/protocol.py."""
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class Paper:
        source: str
        title: str
        authors: list
        abstract: str
        url: str
        pdf_url: Optional[str] = None
        full_text: Optional[str] = None
        tldr: Optional[str] = None
        affiliations: Optional[list] = None
        score: Optional[float] = None

    mod = types.ModuleType("zotero_arxiv_daily.protocol")
    mod.Paper = Paper
    return mod


# Register stubs before importing construct_email
_proto = _make_protocol_stub()

# Build the parent package stub so relative imports work
_pkg = types.ModuleType("zotero_arxiv_daily")
_pkg.protocol = _proto
sys.modules["zotero_arxiv_daily"] = _pkg
sys.modules["zotero_arxiv_daily.protocol"] = _proto

# Add src/ to path so "import zotero_arxiv_daily.construct_email" resolves
_src = os.path.join(os.path.dirname(__file__), "..", "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# We patch ZOTERO_ADD_URL via env var before importing
os.environ["ZOTERO_ADD_URL"] = "https://zotero-add.example.workers.dev"

# Load construct_email.py from the scratchpad directly as part of the stub package
import importlib.util as _ilu
_here = os.path.dirname(__file__)
_spec = _ilu.spec_from_file_location(
    "zotero_arxiv_daily.construct_email",
    os.path.join(_here, "construct_email.py"),
    submodule_search_locations=[],
)
ce = _ilu.module_from_spec(_spec)
ce.__package__ = "zotero_arxiv_daily"
sys.modules["zotero_arxiv_daily.construct_email"] = ce
_spec.loader.exec_module(ce)

Paper = _proto.Paper


# ===========================================================================
# 1. Unit tests — no network required
# ===========================================================================

class TestExtractArxivId(unittest.TestCase):
    """_extract_arxiv_id must handle several URL shapes."""

    def test_standard_url(self):
        self.assertEqual(
            ce._extract_arxiv_id("https://arxiv.org/abs/2310.12345"),
            "2310.12345",
        )

    def test_url_with_version(self):
        self.assertEqual(
            ce._extract_arxiv_id("https://arxiv.org/abs/2310.12345v2"),
            "2310.12345v2",
        )

    def test_url_with_trailing_slash(self):
        self.assertEqual(
            ce._extract_arxiv_id("https://arxiv.org/abs/2310.12345/"),
            "2310.12345",
        )

    def test_non_arxiv_url(self):
        self.assertEqual(ce._extract_arxiv_id("https://example.com/paper"), "")

    def test_empty_string(self):
        self.assertEqual(ce._extract_arxiv_id(""), "")


class TestMakeAddToZoteroButton(unittest.TestCase):
    """_make_add_to_zotero_button must produce correct HTML."""

    def test_button_contains_worker_url(self):
        html = ce._make_add_to_zotero_button("2310.12345", "Some TLDR")
        self.assertIn("zotero-add.example.workers.dev", html)
        self.assertIn("/add?", html)

    def test_button_encodes_tldr(self):
        tldr = "This paper introduces a novel method: 2+2=4"
        html = ce._make_add_to_zotero_button("2310.12345", tldr)
        # urlencode uses quote_plus (spaces → "+"), so decode and check the raw value
        decoded = urllib.parse.unquote_plus(html)
        self.assertIn(tldr, decoded)

    def test_button_includes_arxiv_id(self):
        html = ce._make_add_to_zotero_button("2310.12345", "")
        self.assertIn("id=2310.12345", html)

    def test_no_button_when_no_worker_url(self):
        original = ce.ZOTERO_ADD_URL
        try:
            ce.ZOTERO_ADD_URL = ""
            html = ce._make_add_to_zotero_button("2310.12345", "TLDR")
            self.assertEqual(html, "")
        finally:
            ce.ZOTERO_ADD_URL = original

    def test_no_button_when_no_arxiv_id(self):
        html = ce._make_add_to_zotero_button("", "TLDR")
        self.assertEqual(html, "")

    def test_button_label(self):
        html = ce._make_add_to_zotero_button("2310.12345", "")
        self.assertIn("Add to Zotero", html)


class TestGetBlockHtml(unittest.TestCase):
    """get_block_html must include PDF and Zotero buttons."""

    def test_pdf_button_present(self):
        html = ce.get_block_html(
            title="Test Paper", authors="A, B", rate="7.5",
            tldr="Short summary", pdf_url="https://arxiv.org/pdf/2310.12345.pdf",
            affiliations="MIT", arxiv_id="2310.12345",
        )
        self.assertIn("PDF", html)
        self.assertIn("https://arxiv.org/pdf/2310.12345.pdf", html)

    def test_add_to_zotero_button_present(self):
        html = ce.get_block_html(
            title="Test Paper", authors="A, B", rate="7.5",
            tldr="Short summary", pdf_url="https://arxiv.org/pdf/2310.12345.pdf",
            affiliations="MIT", arxiv_id="2310.12345",
        )
        self.assertIn("Add to Zotero", html)
        self.assertIn("2310.12345", html)

    def test_no_add_button_when_no_id(self):
        html = ce.get_block_html(
            title="Test Paper", authors="A, B", rate="7.5",
            tldr="Short summary", pdf_url="https://example.com/paper.pdf",
            affiliations="MIT", arxiv_id="",
        )
        self.assertNotIn("Add to Zotero", html)


class TestRenderEmail(unittest.TestCase):
    """render_email must produce valid HTML with the Zotero button."""

    def _make_paper(self, arxiv_id="2310.12345", tldr="A great finding"):
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

    def test_full_render_contains_add_button(self):
        html = ce.render_email([self._make_paper()])
        self.assertIn("Add to Zotero", html)

    def test_full_render_contains_pdf(self):
        html = ce.render_email([self._make_paper()])
        self.assertIn("PDF", html)

    def test_empty_papers_no_button(self):
        html = ce.render_email([])
        self.assertNotIn("Add to Zotero", html)
        self.assertIn("No Papers Today", html)

    def test_tldr_in_button_url(self):
        tldr = "Transformers outperform RNNs on MT tasks"
        html = ce.render_email([self._make_paper(tldr=tldr)])
        # urlencode uses quote_plus (spaces → "+"); decode and check the raw TLDR
        decoded = urllib.parse.unquote_plus(html)
        self.assertIn(tldr, decoded)


# ===========================================================================
# 2. Integration test — calls real arXiv API (skipped in CI by default)
# ===========================================================================

SKIP_INTEGRATION = os.environ.get("RUN_INTEGRATION_TESTS", "0") != "1"

@unittest.skipIf(SKIP_INTEGRATION, "Set RUN_INTEGRATION_TESTS=1 to run integration tests")
class TestWorkerIntegration(unittest.TestCase):
    """
    Smoke-test the deployed Cloudflare Worker.

    Requires:
        RUN_INTEGRATION_TESTS=1
        WORKER_URL=https://zotero-add.YOUR_SUBDOMAIN.workers.dev
    """

    def setUp(self):
        self.worker_url = os.environ.get("WORKER_URL", "").rstrip("/")
        if not self.worker_url:
            self.skipTest("WORKER_URL not set")

    def test_add_known_paper(self):
        import urllib.request

        arxiv_id = "1706.03762"          # "Attention Is All You Need"
        tldr     = "Integration test TLDR — please ignore"
        url      = (
            f"{self.worker_url}/add"
            f"?id={urllib.parse.quote(arxiv_id)}"
            f"&tldr={urllib.parse.quote(tldr)}"
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode()
        self.assertIn("Zotero", body)
        # Should NOT contain "Errore"
        self.assertNotIn("Errore", body)

    def test_missing_id_returns_400(self):
        import urllib.request
        import urllib.error

        url = f"{self.worker_url}/add"
        try:
            with urllib.request.urlopen(url, timeout=10):
                pass
            self.fail("Expected HTTP 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_invalid_id_handled_gracefully(self):
        import urllib.request

        url = f"{self.worker_url}/add?id=0000.00000"
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read().decode()
        # Should return a page, not crash
        self.assertIn("<!DOCTYPE html>", body)


if __name__ == "__main__":
    unittest.main()
