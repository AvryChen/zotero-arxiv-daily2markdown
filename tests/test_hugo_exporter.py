"""Tests for Hugo markdown rendering."""

from datetime import datetime, timezone
from types import SimpleNamespace

from tests.canned_responses import make_sample_paper
from omegaconf import OmegaConf

from zotero_arxiv_daily2markdown.hugo_exporter import (
    cleanup_empty_hugo_notices,
    export_empty_notice_to_hugo,
    export_to_hugo,
    _hugo_auto_push_enabled,
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
    assert "\nlang: zh\n" not in markdown


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
    assert "\nlang: en\n" not in markdown


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


def test_hugo_auto_push_config_false_overrides_env(monkeypatch):
    monkeypatch.setenv("HUGO_AUTO_PUSH", "true")
    config = OmegaConf.create({"hugo": {"output_dir": "content", "auto_push": False}})

    assert _hugo_auto_push_enabled(config) is False


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
    orphan_generated_notice = tmp_path / "en" / "daily" / "2026-05-20.md"
    orphan_generated_notice.parent.mkdir(parents=True, exist_ok=True)
    orphan_generated_notice.write_text(
        '---\ntitle: stale notice\n---\n\n<section class="arxiv-empty-notice"></section>\n',
        encoding="utf-8",
    )

    artifacts = export_empty_notice_to_hugo(config)

    zh_text = (tmp_path / "zh" / "posts" / "2026-05-21-arxiv-daily.md").read_text(encoding="utf-8")
    en_text = (tmp_path / "en" / "posts" / "2026-05-21-arxiv-daily.md").read_text(encoding="utf-8")
    assert artifacts.date_str == "2026-05-21"
    assert "arxiv_empty_notice: true" in zh_text
    assert "\nlang: zh\n" not in zh_text
    assert "\nlang: en\n" not in en_text
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
        "2026-05-20.md",
        "2026-05-21-arxiv-daily.md",
        "2026-05-21-arxiv-daily.md",
    ]
    assert not (tmp_path / "zh" / "posts" / "2026-05-21-arxiv-daily.md").exists()
    assert not (tmp_path / "en" / "posts" / "2026-05-21-arxiv-daily.md").exists()
    assert not orphan_generated_notice.exists()
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


def test_export_to_hugo_auto_push_stages_daily_json_extra_path(tmp_path, monkeypatch):
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(tmp_path / "content"), "auto_push": True},
        }
    )
    extra_path = tmp_path / "data" / "daily" / "2026-05-21.json"
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_text("{}\n", encoding="utf-8")
    runs = []

    def fake_run(args, **kwargs):
        runs.append(args)
        if args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M data/daily/2026-05-21.json\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.hugo_exporter.subprocess.run", fake_run)

    export_to_hugo(
        [make_sample_paper(tldr="中文总结", tldr_en="English summary")],
        config,
        "overview zh",
        "overview en",
        extra_paths=[extra_path],
    )

    assert any(args[:3] == ["git", "add", "--"] and str(extra_path) in args for args in runs)


def test_export_to_hugo_auto_push_generates_knowledge_pages_and_builds(tmp_path, monkeypatch):
    repo_dir = tmp_path
    content_dir = repo_dir / "content"
    knowledge_dir = repo_dir / "data" / "knowledge"
    knowledge_paths = [
        knowledge_dir / "papers.jsonl",
        knowledge_dir / "paper_insights.json",
        knowledge_dir / "paper_workflows.json",
        knowledge_dir / "aligned_vocabulary.json",
    ]
    extra_path = repo_dir / "data" / "daily" / "2026-05-21.json"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "package.json").write_text("{}\n", encoding="utf-8")
    for path in knowledge_paths:
        path.write_text("{}\n" if path.suffix == ".json" else "{}\n", encoding="utf-8")
    extra_path.write_text("{}\n", encoding="utf-8")
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(content_dir), "auto_push": True},
            "knowledge": {"enabled": True, "output_dir": str(knowledge_dir)},
        }
    )
    runs = []

    def fake_run(args, **kwargs):
        runs.append((args, kwargs))
        if isinstance(args, list) and args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M content/en/posts/2026-05-21-arxiv-daily.md\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.hugo_exporter.subprocess.run", fake_run)

    export_to_hugo(
        [make_sample_paper(tldr="中文总结", tldr_en="English summary")],
        config,
        "overview zh",
        "overview en",
        extra_paths=[extra_path, *knowledge_paths],
    )

    knowledge_demo_runs = [(args, kwargs) for args, kwargs in runs if args == "npm run knowledge:demo"]
    assert len(knowledge_demo_runs) == 1
    assert knowledge_demo_runs[0][1]["cwd"] == repo_dir
    assert knowledge_demo_runs[0][1]["env"]["KNOWLEDGE_DATA_DIR"] == str(knowledge_dir)
    assert any(args == "npm run build" and kwargs["cwd"] == repo_dir for args, kwargs in runs)

    git_adds = [args for args, _ in runs if isinstance(args, list) and args[:3] == ["git", "add", "--"]]
    assert git_adds
    add_args = git_adds[-1]
    assert str(extra_path) in add_args
    assert all(str(path) in add_args for path in knowledge_paths)
    assert str(content_dir / "en" / "daily") in add_args
    assert str(content_dir / "en" / "papers") in add_args
    assert str(content_dir / "en" / "knowledge") in add_args
    assert str(content_dir / "zh" / "daily") in add_args
    assert str(content_dir / "zh" / "papers") in add_args
    assert str(content_dir / "zh" / "knowledge") in add_args
    assert str(content_dir / "en") not in add_args
    assert str(content_dir / "zh") not in add_args


def test_export_to_hugo_auto_push_requires_web_knowledge_inputs_before_build(tmp_path, monkeypatch):
    repo_dir = tmp_path
    content_dir = repo_dir / "content"
    knowledge_dir = repo_dir / "data" / "knowledge"
    daily_path = repo_dir / "data" / "daily" / "2026-05-21.json"
    audit_path = knowledge_dir / "domain_decisions.json"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "package.json").write_text("{}\n", encoding="utf-8")
    for name in ["papers.jsonl", "paper_insights.json", "paper_workflows.json"]:
        (knowledge_dir / name).write_text("{}\n", encoding="utf-8")
    daily_path.write_text("{}\n", encoding="utf-8")
    audit_path.write_text("{}\n", encoding="utf-8")
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(content_dir), "auto_push": True},
            "knowledge": {"enabled": True, "output_dir": str(knowledge_dir)},
        }
    )
    runs = []

    def fake_run(args, **kwargs):
        runs.append((args, kwargs))
        if isinstance(args, list) and args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M content/en/posts/2026-05-21-arxiv-daily.md\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.hugo_exporter.subprocess.run", fake_run)

    export_to_hugo(
        [make_sample_paper(tldr="中文总结", tldr_en="English summary")],
        config,
        "overview zh",
        "overview en",
        extra_paths=[daily_path, audit_path],
    )

    assert not any(args == "npm run knowledge:demo" for args, _ in runs)
    assert not any(args == "npm run build" for args, _ in runs)


def test_export_to_hugo_auto_push_generates_knowledge_pages_from_existing_package(tmp_path, monkeypatch):
    repo_dir = tmp_path
    content_dir = repo_dir / "content"
    knowledge_dir = repo_dir / "data" / "knowledge"
    extra_path = repo_dir / "data" / "daily" / "2026-05-21.json"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "package.json").write_text("{}\n", encoding="utf-8")
    (knowledge_dir / "papers.jsonl").write_text('{"paper_id":"2605.00001"}\n', encoding="utf-8")
    (knowledge_dir / "paper_insights.json").write_text("[]\n", encoding="utf-8")
    (knowledge_dir / "paper_workflows.json").write_text("[]\n", encoding="utf-8")
    (knowledge_dir / "aligned_vocabulary.json").write_text("[]\n", encoding="utf-8")
    extra_path.write_text("{}\n", encoding="utf-8")
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(content_dir), "auto_push": True},
            "knowledge": {"enabled": True, "output_dir": str(knowledge_dir)},
        }
    )
    runs = []

    def fake_run(args, **kwargs):
        runs.append((args, kwargs))
        if isinstance(args, list) and args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M content/en/posts/2026-05-21-arxiv-daily.md\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.hugo_exporter.subprocess.run", fake_run)

    export_to_hugo(
        [make_sample_paper(tldr="中文总结", tldr_en="English summary")],
        config,
        "overview zh",
        "overview en",
        extra_paths=[extra_path],
    )

    assert any(args == "npm run knowledge:demo" for args, _ in runs)
    assert any(args == "npm run build" for args, _ in runs)


def test_export_to_hugo_auto_push_skips_empty_knowledge_package(tmp_path, monkeypatch):
    repo_dir = tmp_path
    content_dir = repo_dir / "content"
    knowledge_dir = repo_dir / "data" / "knowledge"
    knowledge_paths = [
        knowledge_dir / "papers.jsonl",
        knowledge_dir / "paper_insights.json",
        knowledge_dir / "paper_workflows.json",
        knowledge_dir / "aligned_vocabulary.json",
    ]
    extra_path = repo_dir / "data" / "daily" / "2026-05-21.json"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "package.json").write_text("{}\n", encoding="utf-8")
    (knowledge_dir / "papers.jsonl").write_text("", encoding="utf-8")
    (knowledge_dir / "paper_insights.json").write_text("[]\n", encoding="utf-8")
    (knowledge_dir / "paper_workflows.json").write_text("[]\n", encoding="utf-8")
    (knowledge_dir / "aligned_vocabulary.json").write_text("[]\n", encoding="utf-8")
    extra_path.write_text("{}\n", encoding="utf-8")
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(content_dir), "auto_push": True},
            "knowledge": {"enabled": True, "output_dir": str(knowledge_dir)},
        }
    )
    runs = []

    def fake_run(args, **kwargs):
        runs.append((args, kwargs))
        if isinstance(args, list) and args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M content/en/posts/2026-05-21-arxiv-daily.md\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.hugo_exporter.subprocess.run", fake_run)

    export_to_hugo(
        [make_sample_paper(tldr="中文总结", tldr_en="English summary")],
        config,
        "overview zh",
        "overview en",
        extra_paths=[extra_path, *knowledge_paths],
    )

    assert not any(args == "npm run knowledge:demo" for args, _ in runs)
    assert not any(args == "npm run build" for args, _ in runs)


def test_export_to_hugo_auto_push_skips_knowledge_pages_without_fresh_knowledge_paths(tmp_path, monkeypatch):
    repo_dir = tmp_path
    content_dir = repo_dir / "content"
    knowledge_dir = repo_dir / "data" / "knowledge"
    extra_path = repo_dir / "data" / "daily" / "2026-05-21.json"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    (repo_dir / "package.json").write_text("{}\n", encoding="utf-8")
    (knowledge_dir / "papers.jsonl").write_text("{}\n", encoding="utf-8")
    extra_path.write_text("{}\n", encoding="utf-8")
    config = OmegaConf.create(
        {
            "executor": {"target_date": "2026-05-21"},
            "prompt": {"topic": "nickelate superconductors"},
            "hugo": {"output_dir": str(content_dir), "auto_push": True},
            "knowledge": {"enabled": True, "output_dir": str(knowledge_dir)},
        }
    )
    runs = []

    def fake_run(args, **kwargs):
        runs.append((args, kwargs))
        if isinstance(args, list) and args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout=" M content/en/posts/2026-05-21-arxiv-daily.md\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.hugo_exporter.subprocess.run", fake_run)

    export_to_hugo(
        [make_sample_paper(tldr="中文总结", tldr_en="English summary")],
        config,
        "overview zh",
        "overview en",
        extra_paths=[extra_path],
    )

    assert not any(args == "npm run knowledge:demo" for args, _ in runs)
    assert not any(args == "npm run build" for args, _ in runs)
