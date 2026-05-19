"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser

from zotero_arxiv_daily2markdown.retriever.arxiv_retriever import ArxivRetriever, _run_with_hard_timeout
import zotero_arxiv_daily2markdown.retriever.arxiv_retriever as arxiv_retriever


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls
    # arxiv.Client().results(search) which makes real HTTP requests.  We mock
    # the arxiv Client so the test stays offline.
    include_cross_list = config.source.arxiv.get("include_cross_list", False)
    allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
    matched_entries = [
        e for e in mock_feedparser.entries
        if e.get("arxiv_announce_type", "new") in allowed_announce_types
    ]

    # Build fake ArxivResult-like objects matching each RSS entry
    fake_results = []
    for entry in matched_entries:
        pid = entry.id.removeprefix("oai:arXiv.org:")
        fake_results.append(SimpleNamespace(
            title=entry.title,
            authors=[SimpleNamespace(name="Test Author")],
            summary="Test abstract",
            pdf_url=f"https://arxiv.org/pdf/{pid}",
            entry_id=f"https://arxiv.org/abs/{pid}",
            source_url=lambda pid=pid: f"https://arxiv.org/e-print/{pid}",
        ))

    class FakeClient:
        def __init__(self, **kw):
            pass
        def results(self, search):
            requested = set(search.id_list)
            return iter(
                result
                for result in fake_results
                if result.entry_id.removeprefix("https://arxiv.org/abs/") in requested
            )

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)

    # Skip file downloads in convert_to_paper
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(matched_entries)
    assert set(p.title for p in papers) == set(e.title for e in matched_entries)


def test_arxiv_convert_to_paper_skips_full_text_when_disabled(config, monkeypatch):
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: (_ for _ in ()).throw(AssertionError("should not fetch html")))
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: (_ for _ in ()).throw(AssertionError("should not fetch pdf")))
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: (_ for _ in ()).throw(AssertionError("should not fetch tar")))

    retriever = ArxivRetriever(config)
    retriever.fetch_full_text_during_retrieval = False
    raw_paper = SimpleNamespace(
        title="Paper",
        authors=[SimpleNamespace(name="Author")],
        summary="Abstract",
        pdf_url="https://arxiv.org/pdf/2501.00001v1",
        entry_id="https://arxiv.org/abs/2501.00001v1",
        source_url=lambda: "https://arxiv.org/e-print/2501.00001v1",
    )

    paper = retriever.convert_to_paper(raw_paper)

    assert paper.full_text is None


def test_run_with_hard_timeout_returns_value():
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 0.01), timeout=1, operation="test op", paper_title="paper"
    )
    assert result == "done"


def test_run_with_hard_timeout_returns_none_on_timeout(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _sleep_and_return, ("done", 1.0), timeout=0.01, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "timed out" in warnings[0]


def test_run_with_hard_timeout_returns_none_on_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setattr(arxiv_retriever, "logger", SimpleNamespace(warning=warnings.append))
    result = _run_with_hard_timeout(
        _raise_runtime_error, (), timeout=1, operation="test op", paper_title="paper"
    )
    assert result is None
    assert "boom" in warnings[0]
