"""Retriever-specific fixtures."""

import feedparser
import pytest
from pathlib import Path
from types import SimpleNamespace


@pytest.fixture()
def mock_feedparser(monkeypatch):
    """Patch feedparser.parse to return the local RSS fixture for arXiv URLs.

    The arxiv library passes bytes (response.content) to feedparser.parse,
    so we check for both str and bytes URL types.

    Returns the parsed result so tests can assert against it.
    """
    fixture_path = Path("tests/retriever/arxiv_rss_example.xml")
    fixture_bytes = fixture_path.read_bytes()
    parsed = feedparser.parse(str(fixture_path))
    raw_parse = feedparser.parse

    def _patched(url_or_bytes, *args, **kwargs):
        target = url_or_bytes
        if isinstance(target, bytes):
            target = target.decode("utf-8", errors="ignore")
        if isinstance(target, str) and "rss.arxiv.org" in target:
            return parsed
        if isinstance(target, str) and "updates on arXiv.org" in target:
            return parsed
        return raw_parse(url_or_bytes, *args, **kwargs)

    monkeypatch.setattr(feedparser, "parse", _patched)

    def _patched_get(url, **kwargs):
        if "rss.arxiv.org" in url:
            return SimpleNamespace(
                content=fixture_bytes,
                text=fixture_bytes.decode("utf-8"),
                raise_for_status=lambda: None,
            )
        raise AssertionError(f"Unexpected network call in RSS fixture: {url}")

    import requests

    monkeypatch.setattr(requests, "get", _patched_get)
    return parsed
