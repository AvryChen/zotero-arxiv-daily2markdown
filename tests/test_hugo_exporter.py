"""Tests for Hugo markdown rendering."""

from datetime import datetime, timezone

from tests.canned_responses import make_sample_paper
from omegaconf import OmegaConf

from zotero_arxiv_daily2markdown.hugo_exporter import (
    cleanup_empty_hugo_notices,
    export_empty_notice_to_hugo,
    _render_post_markdown,
    _render_empty_notice_markdown,
    extract_hugo_paper_urls,
)


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


def test_export_empty_notice_and_cleanup_only_marked_posts(tmp_path):
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(tmp_path), "auto_push": False},
        }
    )
    normal_post = tmp_path / "zh" / "posts" / "2026-05-20-arxiv-daily.md"
    normal_post.parent.mkdir(parents=True, exist_ok=True)
    normal_post.write_text("---\ntitle: normal\n---\n\n## 1. Paper\n", encoding="utf-8")

    artifacts = export_empty_notice_to_hugo(config)

    zh_text = (tmp_path / "zh" / "posts" / "2026-05-21-arxiv-daily.md").read_text(encoding="utf-8")
    en_text = (tmp_path / "en" / "posts" / "2026-05-21-arxiv-daily.md").read_text(encoding="utf-8")
    assert artifacts.date_str == "2026-05-21"
    assert "arxiv_empty_notice: true" in zh_text
    assert "昨天没有新论文" in zh_text
    assert "为什么今天看到的是昨天的论文？" in zh_text
    assert "New submissions" in zh_text
    assert "Cross-lists" in zh_text
    assert "Replacements" in zh_text
    assert "通常在北京时间 2026-05-22 生成" in zh_text
    assert "No newly matched papers yesterday" in en_text
    assert "Why does today's update process yesterday's papers?" in en_text
    assert "usually generated on 2026-05-22 Beijing time" in en_text

    removed = cleanup_empty_hugo_notices(config)

    assert sorted(path.name for path in removed) == [
        "2026-05-21-arxiv-daily.md",
        "2026-05-21-arxiv-daily.md",
    ]
    assert not (tmp_path / "zh" / "posts" / "2026-05-21-arxiv-daily.md").exists()
    assert not (tmp_path / "en" / "posts" / "2026-05-21-arxiv-daily.md").exists()
    assert normal_post.exists()


def test_empty_notice_raw_html_block_has_no_blank_lines():
    for lang in ("zh", "en"):
        markdown = _render_empty_notice_markdown(
            lang=lang,
            date_str="2026-05-21",
            post_date_time="2026-05-21T20:00:00+08:00",
            topic="nickelate superconductors",
        )
        html_block = markdown[
            markdown.index('<section class="arxiv-empty-notice"') : markdown.rindex("</section>") + len("</section>")
        ]

        assert "\n\n" not in html_block


def test_empty_notice_markdown_keeps_content_outside_code_fences():
    markdown = _render_empty_notice_markdown(
        lang="zh",
        date_str="2026-05-21",
        post_date_time="2026-05-21T20:00:00+08:00",
        topic="nickelate superconductors",
    )

    assert "```" not in markdown
    assert '    <section class="notice-section">' not in markdown
