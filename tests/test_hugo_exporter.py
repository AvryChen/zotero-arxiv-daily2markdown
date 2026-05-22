"""Tests for Hugo markdown rendering."""

from datetime import datetime, timezone

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily2markdown.hugo_exporter import _render_post_markdown, extract_hugo_paper_urls


def test_render_post_markdown_includes_publication_window_zh():
    papers = [
        make_sample_paper(
            title="Earlier Paper",
            tldr="中文总结",
            score=4.0,
            published_at=datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc),
        ),
        make_sample_paper(
            title="Later Paper",
            tldr="中文总结",
            score=3.8,
            published_at=datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
        ),
    ]

    markdown = _render_post_markdown(
        papers,
        "zh",
        "2026-05-19",
        "2026-05-19T20:00:00+08:00",
        "nickelate superconductors",
    )

    assert "**本期论文投稿处理时间范围**" in markdown
    assert "2026-05-19 08:00 至 2026-05-19 12:00（北京时间）" in markdown
    assert "2026-05-19 00:00 至 2026-05-19 04:00 UTC" not in markdown


def test_render_post_markdown_includes_publication_window_en():
    papers = [
        make_sample_paper(
            title="Earlier Paper",
            tldr="中文总结",
            tldr_en="English summary",
            score=4.0,
            published_at=datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc),
        ),
        make_sample_paper(
            title="Later Paper",
            tldr="中文总结",
            tldr_en="English summary",
            score=3.8,
            published_at=datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
        ),
    ]

    markdown = _render_post_markdown(
        papers,
        "en",
        "2026-05-19",
        "2026-05-19T20:00:00+08:00",
        "nickelate superconductors",
    )

    assert "**arXiv submission processing window**" in markdown
    assert "2026-05-19 00:00 to 2026-05-19 04:00 UTC" in markdown
    assert "2026-05-19 08:00 to 2026-05-19 12:00 Beijing time" not in markdown


def test_render_post_markdown_accepts_string_publication_times():
    papers = [
        make_sample_paper(
            title="String Time Paper",
            tldr="中文总结",
            tldr_en="English summary",
            score=4.0,
            published_at="2026-05-19T00:00:00Z",
        ),
    ]

    markdown = _render_post_markdown(
        papers,
        "en",
        "2026-05-19",
        "2026-05-19T20:00:00+08:00",
        "nickelate superconductors",
    )

    assert "2026-05-19 00:00 to 2026-05-19 00:00 UTC" in markdown


def test_extract_hugo_paper_urls_parses_link_lines():
    markdown = """
## 1. Paper
- **Link**: [https://arxiv.org/abs/2605.00001v1](https://arxiv.org/abs/2605.00001v1)

## 2. Another
- **Link**: [https://arxiv.org/abs/2605.00002v1](https://arxiv.org/abs/2605.00002v1)
"""

    assert extract_hugo_paper_urls(markdown) == [
        "https://arxiv.org/abs/2605.00001v1",
        "https://arxiv.org/abs/2605.00002v1",
    ]
