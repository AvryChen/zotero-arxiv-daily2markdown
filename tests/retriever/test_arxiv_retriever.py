"""Tests for ArxivRetriever."""

import time
from types import SimpleNamespace

import feedparser
from omegaconf import open_dict

from zotero_arxiv_daily2markdown.retriever.arxiv_retriever import ArxivRetriever, _run_with_hard_timeout
import zotero_arxiv_daily2markdown.retriever.arxiv_retriever as arxiv_retriever


def _sleep_and_return(value: str, delay_seconds: float) -> str:
    time.sleep(delay_seconds)
    return value


def _raise_runtime_error() -> None:
    raise RuntimeError("boom")


def _rss_response():
    text = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>cond-mat updates on arXiv.org</title>
</feed>
"""
    return SimpleNamespace(content=text.encode(), text=text, raise_for_status=lambda: None)


def _atom_response():
    text = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2605.00001v1</id>
    <title>Paper</title>
    <updated>2026-05-12T01:00:00Z</updated>
    <link href="https://arxiv.org/pdf/2605.00001v1" rel="related" type="application/pdf" title="pdf"/>
    <summary>Abstract.</summary>
    <author><name>Author</name></author>
  </entry>
</feed>
"""
    return SimpleNamespace(content=text.encode(), text=text, raise_for_status=lambda: None)


def test_arxiv_retriever(config, mock_feedparser, monkeypatch):
    arxiv_retriever._reset_arxiv_request_throttle()

    # The RSS fixture gives us paper IDs.  After feedparser, the code calls the
    # arXiv API metadata endpoint, so we mock that metadata lookup.
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

    def fake_fetch_metadata(self, ids, report):
        requested = set(ids)
        results = [
            result
            for result in fake_results
            if result.entry_id.removeprefix("https://arxiv.org/abs/") in requested
        ]
        report.fetched_ids = ids
        report.fetched_count = len(results)
        return results

    monkeypatch.setattr(ArxivRetriever, "_fetch_metadata_by_ids", fake_fetch_metadata)

    # Skip file downloads in convert_to_paper
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_html", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_pdf", lambda paper: None)
    monkeypatch.setattr(arxiv_retriever, "extract_text_from_tar", lambda paper: None)

    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()

    assert len(papers) == len(matched_entries)
    assert set(p.title for p in papers) == set(e.title for e in matched_entries)


def test_arxiv_rss_retries_after_connect_timeout(config, monkeypatch):
    with open_dict(config):
        config.executor.arxiv_rss_retries = 2
        config.executor.arxiv_rss_retry_base_seconds = 0
        config.executor.arxiv_request_interval_seconds = 0
        config.executor.arxiv_rss_cooldown_retries = 0
    arxiv_retriever._reset_arxiv_request_throttle()

    responses = [
        arxiv_retriever.requests.ConnectTimeout("connect timeout"),
        _rss_response(),
    ]
    sleeps = []

    def fake_get(url, **kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)
    monkeypatch.setattr(arxiv_retriever.time, "sleep", lambda seconds: sleeps.append(seconds))

    retriever = ArxivRetriever(config)
    feed = retriever._fetch_arxiv_rss_feed("cond-mat")

    assert feed.feed.title == "cond-mat updates on arXiv.org"
    assert sleeps == [0]


def test_arxiv_rss_cools_down_after_repeated_connect_timeouts(config, monkeypatch):
    with open_dict(config):
        config.executor.arxiv_rss_retries = 2
        config.executor.arxiv_rss_retry_base_seconds = 0
        config.executor.arxiv_request_interval_seconds = 0
        config.executor.arxiv_rss_cooldown_retries = 1
        config.executor.arxiv_rss_cooldown_seconds = 90
    arxiv_retriever._reset_arxiv_request_throttle()

    responses = [
        arxiv_retriever.requests.ConnectTimeout("connect timeout"),
        arxiv_retriever.requests.ConnectTimeout("connect timeout"),
        _rss_response(),
    ]
    sleeps = []

    def fake_get(url, **kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)
    monkeypatch.setattr(arxiv_retriever.time, "sleep", lambda seconds: sleeps.append(seconds))

    retriever = ArxivRetriever(config)
    feed = retriever._fetch_arxiv_rss_feed("cond-mat")

    assert feed.feed.title == "cond-mat updates on arXiv.org"
    assert sleeps == [0, 90]


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


def test_arxiv_requests_share_scheduler_and_user_agent(config, monkeypatch):
    with open_dict(config):
        config.executor.arxiv_request_interval_seconds = 7
        config.executor.arxiv_cache_enabled = False

    calls = []

    def fake_perform(interval, request_func):
        calls.append(("schedule", interval))
        return request_func()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("headers", {}).get("User-Agent"), kwargs.get("proxies")))
        if "rss.arxiv.org" in url:
            return _rss_response()
        if url == arxiv_retriever.ARXIV_API_URL:
            return _atom_response()
        if "/html/" in url:
            return SimpleNamespace(content=b"<html>paper html</html>", text="<html>paper html</html>", raise_for_status=lambda: None)
        if "/pdf/" in url:
            return SimpleNamespace(content=b"%PDF fake", text="", raise_for_status=lambda: None)
        if "/e-print/" in url:
            return SimpleNamespace(content=b"fake tar", text="", raise_for_status=lambda: None)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(arxiv_retriever, "_perform_arxiv_request", fake_perform)
    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)
    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: "HTML text")
    monkeypatch.setattr(arxiv_retriever, "_run_with_hard_timeout", lambda func, args, **kwargs: func(*args))
    monkeypatch.setattr(arxiv_retriever, "extract_markdown_from_pdf", lambda path: "PDF text")
    monkeypatch.setattr(arxiv_retriever, "extract_tex_code_from_tar", lambda path, paper_id: {"all": "TEX text"})

    retriever = ArxivRetriever(config)
    paper = SimpleNamespace(
        title="Paper",
        entry_id="https://arxiv.org/abs/2605.00001v1",
        pdf_url="https://arxiv.org/pdf/2605.00001v1",
        source_url=lambda: "https://arxiv.org/e-print/2605.00001v1",
    )

    retriever._fetch_arxiv_rss_feed("cond-mat")
    retriever._fetch_arxiv_query_page("cat:cond-mat", 0, 1)
    assert retriever.extract_text_from_html(paper) == "HTML text"
    assert retriever.extract_text_from_pdf(paper) == "PDF text"
    assert retriever.extract_text_from_tar(paper) == "TEX text"

    scheduled = [call for call in calls if call[0] == "schedule"]
    assert scheduled == [("schedule", 7)] * 5
    requested = [call for call in calls if call[0] != "schedule"]
    assert [call[0] for call in requested] == [
        "https://rss.arxiv.org/atom/cond-mat",
        arxiv_retriever.ARXIV_API_URL,
        "https://arxiv.org/html/2605.00001v1",
        "https://arxiv.org/pdf/2605.00001v1",
        "https://arxiv.org/e-print/2605.00001v1",
    ]
    assert all(user_agent == config.executor.arxiv_user_agent for _url, user_agent, _proxies in requested)
    assert all(proxies is None for _url, _user_agent, proxies in requested)


def test_arxiv_proxy_config_is_used_for_all_request_paths(config, monkeypatch):
    proxy_url = "http://127.0.0.1:10809"
    expected_proxies = {"http": proxy_url, "https": proxy_url}
    with open_dict(config):
        config.executor.arxiv_request_interval_seconds = 0
        config.executor.arxiv_cache_enabled = False
        config.executor.arxiv_proxy_enabled = True
        config.executor.arxiv_proxy_url = proxy_url

    calls = []

    def fake_perform(interval, request_func):
        return request_func()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("proxies")))
        if "rss.arxiv.org" in url:
            return _rss_response()
        if url == arxiv_retriever.ARXIV_API_URL:
            return _atom_response()
        if "/html/" in url:
            return SimpleNamespace(content=b"<html>paper html</html>", text="<html>paper html</html>", raise_for_status=lambda: None)
        if "/pdf/" in url:
            return SimpleNamespace(content=b"%PDF fake", text="", raise_for_status=lambda: None)
        if "/e-print/" in url:
            return SimpleNamespace(content=b"fake tar", text="", raise_for_status=lambda: None)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(arxiv_retriever, "_perform_arxiv_request", fake_perform)
    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)
    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: "HTML text")
    monkeypatch.setattr(arxiv_retriever, "_run_with_hard_timeout", lambda func, args, **kwargs: func(*args))
    monkeypatch.setattr(arxiv_retriever, "extract_markdown_from_pdf", lambda path: "PDF text")
    monkeypatch.setattr(arxiv_retriever, "extract_tex_code_from_tar", lambda path, paper_id: {"all": "TEX text"})

    retriever = ArxivRetriever(config)
    paper = SimpleNamespace(
        title="Paper",
        entry_id="https://arxiv.org/abs/2605.00001v1",
        pdf_url="https://arxiv.org/pdf/2605.00001v1",
        source_url=lambda: "https://arxiv.org/e-print/2605.00001v1",
    )

    retriever._fetch_arxiv_rss_feed("cond-mat")
    retriever._fetch_arxiv_query_page("cat:cond-mat", 0, 1)
    retriever.extract_text_from_html(paper)
    retriever.extract_text_from_pdf(paper)
    retriever.extract_text_from_tar(paper)

    assert [call[0] for call in calls] == [
        "https://rss.arxiv.org/atom/cond-mat",
        arxiv_retriever.ARXIV_API_URL,
        "https://arxiv.org/html/2605.00001v1",
        "https://arxiv.org/pdf/2605.00001v1",
        "https://arxiv.org/e-print/2605.00001v1",
    ]
    assert all(proxies == expected_proxies for _url, proxies in calls)


def test_arxiv_socks_proxy_url_is_passed_through(config):
    with open_dict(config):
        config.executor.arxiv_proxy_enabled = True
        config.executor.arxiv_proxy_url = "socks5h://127.0.0.1:10808"

    assert ArxivRetriever(config)._arxiv_proxies() == {
        "http": "socks5h://127.0.0.1:10808",
        "https": "socks5h://127.0.0.1:10808",
    }


def test_arxiv_cache_hit_avoids_network_and_corrupt_fulltext_cache_falls_back(config, monkeypatch, tmp_path):
    with open_dict(config):
        config.executor.arxiv_cache_enabled = True
        config.executor.arxiv_cache_dir = str(tmp_path)
        config.executor.arxiv_request_interval_seconds = 0

    network_calls = []

    def fake_get(url, **kwargs):
        network_calls.append(url)
        return _atom_response()

    monkeypatch.setattr(arxiv_retriever.requests, "get", fake_get)

    retriever = ArxivRetriever(config)
    params = {"search_query": "cat:cond-mat", "start": 0}
    first = retriever._arxiv_get_text(arxiv_retriever.ARXIV_API_URL, params=params, cache_kind="api_query")
    second = retriever._arxiv_get_text(arxiv_retriever.ARXIV_API_URL, params=params, cache_kind="api_query")

    assert first == second
    assert network_calls == [arxiv_retriever.ARXIV_API_URL]

    corrupt_path = tmp_path / f"{arxiv_retriever._safe_cache_name('fulltext', {'paper_id': '2605.00001v1'})}.json"
    corrupt_path.write_text("{not json", encoding="utf-8")
    assert retriever._read_full_text_cache("2605.00001v1") is None


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
