"""Integrity tests for arXiv fetching and dailyarxiv cross-validation."""

from types import SimpleNamespace

import pytest
from omegaconf import open_dict

import zotero_arxiv_daily2markdown.retriever.arxiv_retriever as arxiv_retriever
from zotero_arxiv_daily2markdown.retriever.arxiv_retriever import (
    ARXIV_API_URL,
    DAILY_ARXIV_URL,
    ArxivFetchIntegrityError,
    ArxivFetchReport,
    ArxivRetriever,
    build_announcement_window,
)


def _atom_feed(ids: list[str], total: int | None = None) -> str:
    total_results = len(ids) if total is None else total
    entries = "\n".join(
        f"""
  <entry>
    <id>http://arxiv.org/abs/{paper_id}</id>
    <title>Paper {paper_id}</title>
    <updated>2026-05-12T01:00:00Z</updated>
    <link href="https://arxiv.org/abs/{paper_id}" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/{paper_id}" rel="related" type="application/pdf" title="pdf"/>
    <summary>Abstract for {paper_id}.</summary>
    <category term="cond-mat.mtrl-sci" scheme="http://arxiv.org/schemas/atom"/>
    <published>2026-05-12T01:00:00Z</published>
    <arxiv:primary_category term="cond-mat.mtrl-sci"/>
    <author><name>Author {paper_id}</name></author>
  </entry>
"""
        for paper_id in ids
    )
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <opensearch:itemsPerPage>800</opensearch:itemsPerPage>
  <opensearch:totalResults>{total_results}</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  {entries}
</feed>
"""


def _response(text: str):
    return SimpleNamespace(text=text, content=text.encode(), raise_for_status=lambda: None)


def _http_error_response(status_code: int, *, headers: dict[str, str] | None = None):
    response = SimpleNamespace(
        text="",
        content=b"",
        status_code=status_code,
        headers=headers or {},
    )

    def raise_for_status():
        raise arxiv_retriever.requests.HTTPError(f"{status_code} Client Error", response=response)

    response.raise_for_status = raise_for_status
    return response


def _cond_mat_retriever(config, *, strict=True, cross_validate=False, mode="warn") -> ArxivRetriever:
    with open_dict(config):
        config.source.arxiv.category = ["cond-mat"]
        config.executor.fetch_strict = strict
        config.executor.cross_validate_dailyarxiv = cross_validate
        config.executor.cross_validation_mode = mode
    return ArxivRetriever(config)


@pytest.mark.parametrize(
    ("target_date", "expected"),
    [
        ("2026-05-12", ("202605112000", "202605121959")),
        ("2026-05-16", ("202605152000", "202605161959")),
        ("2026-05-17", ("202605162000", "202605171959")),
        ("2026-05-18", ("202605172000", "202605181959")),
    ],
)
def test_announcement_window_does_not_skip_weekends(target_date, expected):
    assert build_announcement_window(target_date) == expected


def test_target_date_empty_results_returns_empty_report(config, monkeypatch):
    retriever = _cond_mat_retriever(config)

    def fake_get(url, **kwargs):
        assert url == ARXIV_API_URL
        return _response(_atom_feed([], total=0))

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    assert retriever._retrieve_by_target_date("2026-05-12") == []
    assert retriever.last_fetch_report.expected_count == 0
    assert retriever.last_fetch_report.fetched_count == 0


def test_target_date_fetch_failure_reports_failed_page(config, monkeypatch):
    retriever = _cond_mat_retriever(config)

    def fake_get(url, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    with pytest.raises(ArxivFetchIntegrityError, match="failed_pages"):
        retriever._retrieve_by_target_date("2026-05-12")

    assert "network down" in retriever.last_fetch_report.summary()


def test_target_date_retries_after_429(config, monkeypatch):
    retriever = _cond_mat_retriever(config)
    with open_dict(config):
        config.executor.arxiv_query_retries = 2
        config.executor.arxiv_retry_base_seconds = 0

    responses = [
        _http_error_response(429),
        _response(_atom_feed(["2605.00001v1"], total=1)),
    ]
    sleeps = []

    def fake_get(url, **kwargs):
        assert url == ARXIV_API_URL
        return responses.pop(0)

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)
    monkeypatch.setattr(arxiv_retriever.time, "sleep", lambda seconds: sleeps.append(seconds))

    papers = retriever._retrieve_by_target_date("2026-05-12")

    assert len(papers) == 1
    assert sleeps == [0]
    assert retriever.last_fetch_report.failed_pages == []


def test_target_date_retry_uses_retry_after_header(config, monkeypatch):
    retriever = _cond_mat_retriever(config)
    with open_dict(config):
        config.executor.arxiv_query_retries = 2
        config.executor.arxiv_retry_base_seconds = 0

    responses = [
        _http_error_response(429, headers={"Retry-After": "7"}),
        _response(_atom_feed([], total=0)),
    ]
    sleeps = []

    monkeypatch.setattr(arxiv_retriever.requests, "get", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(arxiv_retriever.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert retriever._retrieve_by_target_date("2026-05-12") == []
    assert sleeps == [7.0]


def test_target_date_pagination_shortfall_fails_in_strict_mode(config, monkeypatch):
    retriever = _cond_mat_retriever(config, strict=True)

    def fake_get(url, **kwargs):
        start = kwargs["params"]["start"]
        if start == 0:
            return _response(_atom_feed(["2605.00001v1", "2605.00002v1"], total=3))
        return _response(_atom_feed([], total=3))

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    with pytest.raises(ArxivFetchIntegrityError):
        retriever._retrieve_by_target_date("2026-05-12")


def test_metadata_lookup_reports_missing_requested_ids(config, monkeypatch):
    retriever = _cond_mat_retriever(config, strict=True)
    report = ArxivFetchReport(mode="rss", expected_count=3, feed_ids=["2605.00001v1", "2605.00002v1", "2605.00003v1"])

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def results(self, search):
            return iter(
                [
                    SimpleNamespace(entry_id="http://arxiv.org/abs/2605.00001v1"),
                    SimpleNamespace(entry_id="http://arxiv.org/abs/2605.00002v1"),
                ]
            )

    monkeypatch.setattr(arxiv_retriever.arxiv, "Client", FakeClient)
    retriever._fetch_metadata_by_ids(report.feed_ids, report)

    assert report.missing_ids == ["2605.00003v1"]
    with pytest.raises(ArxivFetchIntegrityError):
        retriever._finalize_report(report)


def test_dailyarxiv_cross_validation_warns_without_failing(config, monkeypatch):
    retriever = _cond_mat_retriever(config, cross_validate=True, mode="warn")

    def fake_get(url, **kwargs):
        if url == ARXIV_API_URL:
            return _response(_atom_feed(["2605.00001v1", "2605.00002v1"], total=2))
        if url == DAILY_ARXIV_URL:
            return _response(_atom_feed(["2605.00001v1", "2605.00002v1", "2605.00003v1"], total=3))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    papers = retriever._retrieve_by_target_date("2026-05-12")

    assert len(papers) == 2
    assert retriever.last_fetch_report.dailyarxiv_count == 3
    assert retriever.last_fetch_report.cross_validation_missing_ids == ["2605.00003v1"]


def test_dailyarxiv_cross_validation_can_fail(config, monkeypatch):
    retriever = _cond_mat_retriever(config, cross_validate=True, mode="fail")

    def fake_get(url, **kwargs):
        if url == ARXIV_API_URL:
            return _response(_atom_feed(["2605.00001v1"], total=1))
        if url == DAILY_ARXIV_URL:
            return _response(_atom_feed(["2605.00001v1", "2605.00002v1"], total=2))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    with pytest.raises(ArxivFetchIntegrityError):
        retriever._retrieve_by_target_date("2026-05-12")


def test_dailyarxiv_unavailable_can_fail(config, monkeypatch):
    retriever = _cond_mat_retriever(config, cross_validate=True, mode="fail")

    def fake_get(url, **kwargs):
        if url == ARXIV_API_URL:
            return _response(_atom_feed(["2605.00001v1"], total=1))
        if url == DAILY_ARXIV_URL:
            raise OSError("dailyarxiv down")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    with pytest.raises(ArxivFetchIntegrityError, match="dailyarxiv"):
        retriever._retrieve_by_target_date("2026-05-12")


def test_dailyarxiv_cross_validation_disabled_does_not_call_third_party(config, monkeypatch):
    retriever = _cond_mat_retriever(config, cross_validate=False)

    def fake_get(url, **kwargs):
        assert url == ARXIV_API_URL
        return _response(_atom_feed(["2605.00001v1"], total=1))

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    papers = retriever._retrieve_by_target_date("2026-05-12")

    assert len(papers) == 1
    assert retriever.last_fetch_report.dailyarxiv_count is None


@pytest.mark.live_arxiv
def test_live_arxiv_and_dailyarxiv_contract_smoke(config):
    retriever = _cond_mat_retriever(config, cross_validate=True, mode="warn")
    papers = retriever._retrieve_by_target_date("2026-05-12")

    assert retriever.last_fetch_report.expected_count == len(papers)
    assert retriever.last_fetch_report.dailyarxiv_count is not None
