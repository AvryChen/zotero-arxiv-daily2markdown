import json
from pathlib import Path

from zotero_arxiv_daily2markdown.legacy_hugo_migrator import migrate_legacy_hugo_capture
from zotero_arxiv_daily2markdown.protocol import DomainDecision


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_migrate_legacy_hugo_capture_writes_capture_and_backup(tmp_path):
    content_dir = tmp_path / "content"
    output_dir = tmp_path / "capture"
    old_zh = content_dir / "zh" / "posts" / "2026-04-30-arxiv-daily.md"
    old_en = content_dir / "en" / "posts" / "2026-04-30-arxiv-daily.md"
    new_zh = content_dir / "zh" / "posts" / "2026-05-01-arxiv-daily.md"

    _write(
        old_zh,
        """---
title: "daily 2026-04-30"
date: 2026-04-30T20:00:00+08:00
lang: zh
---

> **今日速览**：
> 中文概览。
> **本期论文投稿处理时间范围**：2026-04-30 09:47 至 2026-05-01 02:52（北京时间）。

## 1. Nickelate Paper
- **关联度评分**: `4.9382`
- **作者**: Author A, Author B
- **机构**: Institute A, Institute B
- **链接**: [http://arxiv.org/abs/2604.28054v1](http://arxiv.org/abs/2604.28054v1)

**总结**: 中文总结。

---
""",
    )
    _write(
        old_en,
        """---
title: "daily 2026-04-30"
date: 2026-04-30T20:00:00+08:00
lang: en
---

> **Daily Overview**:
> English overview.
> **arXiv submission processing window**: 2026-04-30 01:47 to 2026-04-30 18:52 UTC.

## 1. Nickelate Paper
- **Relevance Score**: `4.9382`
- **Authors**: Author A, Author B
- **Affiliations**: Institute A, Institute B
- **Link**: [http://arxiv.org/abs/2604.28054v1](http://arxiv.org/abs/2604.28054v1)

**Summary**: English summary.

---
""",
    )
    _write(
        new_zh,
        """---
title: "daily 2026-05-01"
date: 2026-05-01T20:00:00+08:00
lang: zh
---

## 1. Should Not Migrate
- **链接**: [http://arxiv.org/abs/2605.00001v1](http://arxiv.org/abs/2605.00001v1)

**总结**: skip me.
""",
    )

    def fake_classify(papers, config, openai_client):
        assert papers[0].abstract == "English summary."
        assert papers[0].tldr == "中文总结。"
        assert papers[0].tldr_en == "English summary."
        assert papers[0].categories == []
        decisions = [
            DomainDecision(
                paper_id=paper.arxiv_id,
                is_in_domain=True,
                confidence=0.95,
                decision="accept",
                reason="direct nickelate",
                matched_concepts=["LaNiO3"],
                accepted=True,
            )
            for paper in papers
        ]
        for paper, decision in zip(papers, decisions, strict=True):
            paper.domain_decision = decision
        return decisions

    def fake_populate_full_text(paper):
        paper.full_text = "Real full text from arXiv"
        paper.full_text_source = "html"
        paper.pdf_bytes = b"%PDF real"
        return paper

    result = migrate_legacy_hugo_capture(
        content_dir=content_dir,
        output_dir=output_dir,
        cutoff_date="2026-04-30",
        backup=True,
        backup_root=tmp_path / "backups",
        timestamp="20260522T120000",
        openai_client=object(),
        classify_fn=fake_classify,
        full_text_populator=fake_populate_full_text,
        hugo_output_dir=content_dir,
    )

    papers = [json.loads(line) for line in (output_dir / "papers.jsonl").read_text(encoding="utf-8").splitlines()]
    run_report = json.loads((output_dir / "runs" / "2026-04-30.json").read_text(encoding="utf-8"))
    meta = json.loads((output_dir / "fulltext" / "arxiv" / "2604.28054v1.meta.json").read_text(encoding="utf-8"))
    text = (output_dir / "fulltext" / "arxiv" / "2604.28054v1.txt").read_text(encoding="utf-8")
    manifest = json.loads((output_dir / "runs" / "legacy_migration_manifest.json").read_text(encoding="utf-8"))

    assert result.processed_dates == ["2026-04-30"]
    assert result.paper_count == 1
    assert papers[0]["paper_id"] == "2604.28054v1"
    assert papers[0]["abstract"] == "English summary."
    assert papers[0]["categories"] == []
    assert papers[0]["summary_zh"] == "中文总结。"
    assert papers[0]["summary_en"] == "English summary."
    assert papers[0]["domain_decision"]["decision"] == "accept"
    assert text == "Real full text from arXiv"
    assert meta["full_text_source"] == "html"
    assert meta["full_text_available"] is True
    assert meta["pdf_path"].endswith("2604.28054v1.pdf")
    assert run_report["migration_source"] == "legacy_hugo_markdown"
    assert run_report["accepted_count"] == 1
    assert run_report["rejected_count"] == 0
    assert "Nickelate Paper" in old_zh.read_text(encoding="utf-8")
    assert manifest["cutoff_date"] == "2026-04-30"
    assert manifest["processed_dates"] == ["2026-04-30"]
    assert not (output_dir / "runs" / "2026-05-01.json").exists()
    assert (tmp_path / "backups" / "legacy_hugo_capture_20260522T120000" / "legacy_hugo" / "zh" / "posts" / old_zh.name).exists()


def test_migrate_legacy_hugo_capture_does_not_copy_backup_tree_into_existing_capture_backup(tmp_path):
    content_dir = tmp_path / "content"
    output_dir = tmp_path / "capture"
    _write(
        content_dir / "zh" / "posts" / "2026-04-30-arxiv-daily.md",
        """---
lang: zh
---

## 1. Paper
- **链接**: [http://arxiv.org/abs/2604.00001v1](http://arxiv.org/abs/2604.00001v1)

**总结**: summary.
""",
    )
    _write(output_dir / "runs" / "2026-05-19.json", "{}\n")
    _write(output_dir / "backups" / "old_backup" / "marker.txt", "must not recurse")

    def fake_classify(papers, config, openai_client):
        decisions = [
            DomainDecision(
                paper_id=paper.arxiv_id,
                is_in_domain=False,
                confidence=0.99,
                decision="reject",
                reason="off topic",
            )
            for paper in papers
        ]
        for paper, decision in zip(papers, decisions, strict=True):
            paper.domain_decision = decision
        return decisions

    migrate_legacy_hugo_capture(
        content_dir=content_dir,
        output_dir=output_dir,
        cutoff_date="2026-04-30",
        backup=True,
        timestamp="20260522T130000",
        openai_client=object(),
        classify_fn=fake_classify,
        full_text_populator=lambda paper: paper,
    )

    backup_dir = output_dir / "backups" / "legacy_hugo_capture_20260522T130000"
    assert (backup_dir / "existing_capture" / "runs" / "2026-05-19.json").exists()
    assert not (backup_dir / "existing_capture" / "backups").exists()


def test_migrate_legacy_hugo_capture_deletes_hugo_post_when_day_has_no_accepts(tmp_path):
    content_dir = tmp_path / "content"
    output_dir = tmp_path / "capture"
    zh_post = content_dir / "zh" / "posts" / "2026-04-30-arxiv-daily.md"
    en_post = content_dir / "en" / "posts" / "2026-04-30-arxiv-daily.md"
    _write(
        zh_post,
        """---
lang: zh
---

## 1. Off Topic
- **链接**: [http://arxiv.org/abs/2604.00001v1](http://arxiv.org/abs/2604.00001v1)

**总结**: summary.
""",
    )
    _write(en_post, zh_post.read_text(encoding="utf-8").replace("lang: zh", "lang: en"))

    def fake_classify(papers, config, openai_client):
        decisions = [
            DomainDecision(
                paper_id=paper.arxiv_id,
                is_in_domain=False,
                confidence=0.99,
                decision="reject",
                reason="not nickelate",
            )
            for paper in papers
        ]
        for paper, decision in zip(papers, decisions, strict=True):
            paper.domain_decision = decision
        return decisions

    def fail_if_called(paper):
        raise AssertionError("Full text should only be fetched for accepted papers.")

    migrate_legacy_hugo_capture(
        content_dir=content_dir,
        output_dir=output_dir,
        cutoff_date="2026-04-30",
        backup=True,
        timestamp="20260522T140000",
        openai_client=object(),
        classify_fn=fake_classify,
        full_text_populator=fail_if_called,
        hugo_output_dir=content_dir,
    )

    run_report = json.loads((output_dir / "runs" / "2026-04-30.json").read_text(encoding="utf-8"))
    rejected = [json.loads(line) for line in (output_dir / "rejected_candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert run_report["accepted_count"] == 0
    assert run_report["rejected_count"] == 1
    assert rejected[0]["paper_id"] == "2604.00001v1"
    assert not zh_post.exists()
    assert not en_post.exists()
