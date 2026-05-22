from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import dotenv
from loguru import logger
from openai import OpenAI
from omegaconf import OmegaConf

from .capture_exporter import export_capture_artifacts
from .domain_classifier import classify_domain_papers
from .hugo_exporter import build_hugo_export_artifacts
from .protocol import DomainDecision, Paper
from .retriever.arxiv_retriever import ArxivRetriever


DEFAULT_CUTOFF_DATE = "2026-04-30"


@dataclass
class LegacyHugoPaper:
    run_date: str
    index: int
    title: str
    url: str
    arxiv_id: str
    score: float | None = None
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    summary_zh: str = ""
    summary_en: str = ""
    source_paths: list[str] = field(default_factory=list)


@dataclass
class ParsedLegacyPost:
    run_date: str
    lang: str
    path: Path
    overview: str = ""
    processing_window: str = ""
    papers: list[LegacyHugoPaper] = field(default_factory=list)


@dataclass
class MigrationResult:
    processed_dates: list[str]
    generated_hugo_dates: list[str]
    removed_hugo_dates: list[str]
    paper_count: int
    accepted_count: int
    rejected_count: int
    uncertain_count: int
    output_dir: str
    backup_dir: str | None
    manifest_path: str


def migrate_legacy_hugo_capture(
    *,
    content_dir: str | Path,
    output_dir: str | Path = "data/capture",
    cutoff_date: str = DEFAULT_CUTOFF_DATE,
    backup: bool = True,
    backup_root: str | Path | None = None,
    timestamp: str | None = None,
    fetch_full_text: bool = True,
    hugo_output_dir: str | Path | None = None,
    hugo_auto_push: bool = False,
    replace_legacy: bool = True,
    runtime_config: Any | None = None,
    openai_client: Any | None = None,
    classify_fn: Callable[[list[Paper], Any, Any], list[DomainDecision]] = classify_domain_papers,
    full_text_populator: Callable[[Paper], Paper] | None = None,
) -> MigrationResult:
    content_path = Path(content_dir).expanduser()
    output_path = Path(output_dir).expanduser()
    hugo_output_path = Path(hugo_output_dir).expanduser() if hugo_output_dir else content_path
    cutoff = date.fromisoformat(cutoff_date)
    grouped_posts = _load_legacy_posts(content_path, cutoff)
    candidate_ids = {
        item.arxiv_id
        for posts in grouped_posts.values()
        for item in _merge_posts(posts).copy()
    }
    timestamp = timestamp or datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    backup_dir = _create_backup(
        content_path=content_path,
        output_path=output_path,
        grouped_posts=grouped_posts,
        backup_root=Path(backup_root).expanduser() if backup_root else output_path / "backups",
        timestamp=timestamp,
    ) if backup else None

    output_path.mkdir(parents=True, exist_ok=True)
    if replace_legacy:
        _remove_existing_legacy_capture(output_path, candidate_ids, grouped_posts.keys())
    runtime_config = runtime_config or _load_runtime_config(output_path)
    openai_client = openai_client or _make_openai_client(runtime_config)
    processed_dates: list[str] = []
    generated_hugo_dates: list[str] = []
    removed_hugo_dates: list[str] = []
    total_papers = 0
    accepted_total = 0
    rejected_total = 0
    uncertain_total = 0
    migration_reports: list[dict[str, Any]] = []
    arxiv_retriever = None
    if full_text_populator is None and fetch_full_text:
        arxiv_retriever = _make_arxiv_retriever(runtime_config, output_path)
    if full_text_populator is None and fetch_full_text and arxiv_retriever is not None:
        full_text_populator = arxiv_retriever.populate_full_text

    for run_date in sorted(grouped_posts):
        posts = grouped_posts[run_date]
        legacy_papers = _merge_posts(posts)
        if not legacy_papers:
            continue
        logger.info(f"Processing legacy Hugo date {run_date}: {len(legacy_papers)} candidate papers")
        papers = [_paper_from_legacy(item) for item in legacy_papers]
        decisions = classify_fn(papers, runtime_config, openai_client)
        accepted = [paper for paper in papers if paper.domain_decision and paper.domain_decision.accepted]
        if full_text_populator:
            for paper in accepted:
                full_text_populator(paper)
        rejected_count = sum(1 for decision in decisions if decision.decision == "reject")
        uncertain_count = sum(1 for decision in decisions if decision.decision == "uncertain")
        source_paths = sorted({str(post.path) for post in posts.values()})
        overview = {lang: post.overview for lang, post in posts.items() if post.overview}
        processing_window = {
            lang: post.processing_window
            for lang, post in posts.items()
            if post.processing_window
        }
        export_capture_artifacts(
            accepted_papers=accepted,
            candidate_papers=papers,
            domain_decisions=decisions,
            config=_capture_config(output_path),
            run_date=run_date,
            report={
                "retrieved_count": len(papers),
                "longlisted_count": len(papers),
                "accepted_count": len(accepted),
                "rejected_count": rejected_count,
                "uncertain_count": uncertain_count,
                "displayed_count": len(accepted),
                "migration_source": "legacy_hugo_markdown",
                "legacy_source_paths": source_paths,
                "legacy_overview": overview,
                "legacy_processing_window": processing_window,
                "legacy_cutoff_date": cutoff_date,
                "legacy_full_text_mode": "arxiv_fetch" if fetch_full_text else "no_full_text_fetch",
                "legacy_domain_input": "legacy_hugo_tldr_without_preaccept_arxiv_metadata",
            },
        )
        if accepted:
            _write_hugo_posts(
                papers=accepted,
                run_date=run_date,
                hugo_output_dir=hugo_output_path,
                runtime_config=runtime_config,
            )
            generated_hugo_dates.append(run_date)
        else:
            _remove_hugo_posts(run_date, hugo_output_path)
            removed_hugo_dates.append(run_date)
        logger.info(
            f"Finished legacy Hugo date {run_date}: "
            f"accepted={len(accepted)} rejected={rejected_count} uncertain={uncertain_count}"
        )
        processed_dates.append(run_date)
        total_papers += len(papers)
        accepted_total += len(accepted)
        rejected_total += rejected_count
        uncertain_total += uncertain_count
        migration_reports.append(
            {
                "run_date": run_date,
                "candidate_count": len(papers),
                "accepted_count": len(accepted),
                "rejected_count": rejected_count,
                "uncertain_count": uncertain_count,
                "source_paths": source_paths,
            }
        )

    if hugo_auto_push:
        _commit_and_push_hugo(hugo_output_path, generated_hugo_dates, removed_hugo_dates)

    manifest_path = output_path / "runs" / "legacy_migration_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        manifest_path,
        {
            "migration_source": "legacy_hugo_markdown",
            "content_dir": str(content_path),
            "output_dir": str(output_path),
            "cutoff_date": cutoff_date,
            "processed_dates": processed_dates,
            "generated_hugo_dates": generated_hugo_dates,
            "removed_hugo_dates": removed_hugo_dates,
            "paper_count": total_papers,
            "accepted_count": accepted_total,
            "rejected_count": rejected_total,
            "uncertain_count": uncertain_total,
            "backup_dir": str(backup_dir) if backup_dir else None,
            "fetch_full_text": fetch_full_text,
            "replace_legacy": replace_legacy,
            "reports": migration_reports,
        },
    )
    return MigrationResult(
        processed_dates=processed_dates,
        generated_hugo_dates=generated_hugo_dates,
        removed_hugo_dates=removed_hugo_dates,
        paper_count=total_papers,
        accepted_count=accepted_total,
        rejected_count=rejected_total,
        uncertain_count=uncertain_total,
        output_dir=str(output_path),
        backup_dir=str(backup_dir) if backup_dir else None,
        manifest_path=str(manifest_path),
    )


def _load_legacy_posts(content_dir: Path, cutoff: date) -> dict[str, dict[str, ParsedLegacyPost]]:
    grouped: dict[str, dict[str, ParsedLegacyPost]] = {}
    for lang in ("zh", "en"):
        for path in sorted((content_dir / lang / "posts").glob("*-arxiv-daily.md")):
            run_date = _date_from_filename(path)
            if not run_date or date.fromisoformat(run_date) > cutoff:
                continue
            post = parse_legacy_hugo_post(path, lang)
            grouped.setdefault(run_date, {})[lang] = post
    return grouped


def parse_legacy_hugo_post(path: str | Path, lang: str | None = None) -> ParsedLegacyPost:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    run_date = _date_from_filename(path)
    if not run_date:
        raise ValueError(f"Cannot infer run date from {path}")
    lang = lang or _frontmatter_value(text, "lang") or _lang_from_path(path)
    post = ParsedLegacyPost(
        run_date=run_date,
        lang=lang,
        path=path,
        overview=_extract_overview(text),
        processing_window=_extract_processing_window(text),
    )
    for match in re.finditer(
        r"^##\s+(?P<index>\d+)\.\s+(?P<title>.+?)\n(?P<body>.*?)(?=^---\s*$|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    ):
        paper = _parse_paper_section(
            run_date=run_date,
            lang=lang,
            index=int(match.group("index")),
            title=_clean_inline(match.group("title")),
            body=match.group("body"),
            source_path=path,
        )
        if paper:
            post.papers.append(paper)
    return post


def _parse_paper_section(
    *,
    run_date: str,
    lang: str,
    index: int,
    title: str,
    body: str,
    source_path: Path,
) -> LegacyHugoPaper | None:
    url = _field_link(body, "链接" if lang == "zh" else "Link")
    if not url:
        return None
    arxiv_id = _extract_arxiv_id(url)
    if not arxiv_id:
        return None
    summary = _summary(body, "总结" if lang == "zh" else "Summary")
    paper = LegacyHugoPaper(
        run_date=run_date,
        index=index,
        title=title,
        url=url,
        arxiv_id=arxiv_id,
        score=_score(body, "关联度评分" if lang == "zh" else "Relevance Score"),
        authors=_split_list(_field_text(body, "作者" if lang == "zh" else "Authors")),
        affiliations=_split_list(_field_text(body, "机构" if lang == "zh" else "Affiliations")),
        summary_zh=summary if lang == "zh" else "",
        summary_en=summary if lang == "en" else "",
        source_paths=[str(source_path)],
    )
    return paper


def _merge_posts(posts: dict[str, ParsedLegacyPost]) -> list[LegacyHugoPaper]:
    merged: dict[str, LegacyHugoPaper] = {}
    for lang in ("zh", "en"):
        post = posts.get(lang)
        if not post:
            continue
        for item in post.papers:
            existing = merged.get(item.arxiv_id)
            if existing is None:
                merged[item.arxiv_id] = item
                continue
            if item.summary_zh:
                existing.summary_zh = item.summary_zh
            if item.summary_en:
                existing.summary_en = item.summary_en
            if item.affiliations and not existing.affiliations:
                existing.affiliations = item.affiliations
            if item.authors and not existing.authors:
                existing.authors = item.authors
            if item.score is not None:
                existing.score = item.score
            for source_path in item.source_paths:
                if source_path not in existing.source_paths:
                    existing.source_paths.append(source_path)
    return sorted(merged.values(), key=lambda item: item.index)


def _paper_from_legacy(item: LegacyHugoPaper) -> Paper:
    paper = Paper(
        source="legacy_hugo",
        title=item.title,
        authors=item.authors,
        abstract=item.summary_en or item.summary_zh,
        url=item.url,
        pdf_url=f"https://arxiv.org/pdf/{item.arxiv_id}",
        full_text=None,
        tldr=item.summary_zh or None,
        tldr_en=item.summary_en or None,
        affiliations=item.affiliations,
        score=item.score,
        arxiv_id=item.arxiv_id,
        domain_decision=None,
        full_text_source=None,
        full_text_errors={
            "legacy_migration": (
                "Original legacy Hugo Markdown did not include raw abstract/categories/full text. "
                "Legacy summaries were used as candidate metadata for domain classification."
            )
        },
    )
    return paper


def _legacy_text(item: LegacyHugoPaper) -> str:
    parts = [
        f"Title: {item.title}",
        f"arXiv: {item.arxiv_id}",
        f"URL: {item.url}",
    ]
    if item.authors:
        parts.append("Authors: " + ", ".join(item.authors))
    if item.affiliations:
        parts.append("Affiliations: " + ", ".join(item.affiliations))
    if item.summary_zh:
        parts.extend(["", "Legacy Chinese summary:", item.summary_zh])
    if item.summary_en:
        parts.extend(["", "Legacy English summary:", item.summary_en])
    return "\n".join(parts).strip()


def _create_backup(
    *,
    content_path: Path,
    output_path: Path,
    grouped_posts: dict[str, dict[str, ParsedLegacyPost]],
    backup_root: Path,
    timestamp: str,
) -> Path:
    backup_dir = backup_root / f"legacy_hugo_capture_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    copied_sources: list[str] = []
    for posts in grouped_posts.values():
        for post in posts.values():
            relative = post.path.relative_to(content_path)
            destination = backup_dir / "legacy_hugo" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(post.path, destination)
            copied_sources.append(str(relative))
    if output_path.exists():
        output_root = output_path.resolve()

        def ignore_capture_backups(src: str, names: list[str]) -> set[str]:
            return {"backups"} if Path(src).resolve() == output_root and "backups" in names else set()

        shutil.copytree(
            output_path,
            backup_dir / "existing_capture",
            dirs_exist_ok=True,
            ignore=ignore_capture_backups,
        )
    _write_json(
        backup_dir / "backup_manifest.json",
        {
            "created_at": datetime.now().astimezone().isoformat(),
            "content_dir": str(content_path),
            "output_dir": str(output_path),
            "source_files": sorted(copied_sources),
            "included_output_backup": output_path.exists(),
        },
    )
    return backup_dir


def _make_arxiv_retriever(runtime_config: Any, output_dir: Path) -> ArxivRetriever:
    config = OmegaConf.create(OmegaConf.to_container(runtime_config, resolve=True))
    config.capture.enabled = True
    config.capture.save_pdf = True
    config.capture.output_dir = str(output_dir)
    config.capture.fulltext_dir = str(output_dir / "fulltext")
    if not config.source.arxiv.get("category"):
        config.source.arxiv.category = ["cond-mat"]
    return ArxivRetriever(config)


def _capture_config(output_dir: Path) -> Any:
    return OmegaConf.create(
        {
            "capture": {
                "enabled": True,
                "output_dir": str(output_dir),
                "fulltext_dir": str(output_dir / "fulltext"),
                "save_pdf": True,
                "save_full_text": True,
                "save_meta": True,
                "save_rejected_candidates": True,
            }
        }
    )


def _load_runtime_config(output_dir: Path) -> Any:
    dotenv.load_dotenv()
    base_path = Path("config/base.yaml")
    custom_path = Path("config/custom.yaml")
    config = OmegaConf.load(base_path)
    if custom_path.exists():
        config = OmegaConf.merge(config, OmegaConf.load(custom_path))
    config.capture.enabled = True
    config.capture.output_dir = str(output_dir)
    config.capture.fulltext_dir = str(output_dir / "fulltext")
    config.executor.debug = False
    config.executor.skip_existing = False
    if not config.source.arxiv.get("category"):
        config.source.arxiv.category = ["cond-mat"]
    return config


def _make_openai_client(config: Any) -> OpenAI:
    return OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)


def _remove_existing_legacy_capture(output_dir: Path, paper_ids: set[str], run_dates: Any) -> None:
    if not paper_ids:
        return
    _filter_jsonl(output_dir / "papers.jsonl", paper_ids)
    _filter_jsonl(output_dir / "rejected_candidates.jsonl", paper_ids)
    _filter_domain_decisions(output_dir / "domain_decisions.json", paper_ids)
    for run_date in run_dates:
        run_path = output_dir / "runs" / f"{run_date}.json"
        if run_path.exists():
            run_path.unlink()
    fulltext_dir = output_dir / "fulltext" / "arxiv"
    for paper_id in paper_ids:
        for suffix in (".txt", ".pdf", ".meta.json"):
            path = fulltext_dir / f"{paper_id}{suffix}"
            if path.exists():
                path.unlink()


def _filter_jsonl(path: Path, paper_ids: set[str]) -> None:
    if not path.exists():
        return
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if str(item.get("paper_id")) not in paper_ids:
            rows.append(item)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + os.linesep for row in rows), encoding="utf-8")


def _filter_domain_decisions(path: Path, paper_ids: set[str]) -> None:
    if not path.exists():
        return
    items = json.loads(path.read_text(encoding="utf-8") or "[]")
    kept = [item for item in items if str(item.get("paper_id")) not in paper_ids]
    _write_json(path, kept)


def _write_hugo_posts(
    *,
    papers: list[Paper],
    run_date: str,
    hugo_output_dir: Path,
    runtime_config: Any,
) -> None:
    config = _hugo_config(run_date, hugo_output_dir, runtime_config)
    artifacts = build_hugo_export_artifacts(papers, config)
    Path(artifacts.filepath_zh).parent.mkdir(parents=True, exist_ok=True)
    Path(artifacts.filepath_en).parent.mkdir(parents=True, exist_ok=True)
    Path(artifacts.filepath_zh).write_text(artifacts.content_zh, encoding="utf-8")
    Path(artifacts.filepath_en).write_text(artifacts.content_en, encoding="utf-8")


def _remove_hugo_posts(run_date: str, hugo_output_dir: Path) -> None:
    for lang in ("zh", "en"):
        path = hugo_output_dir / lang / "posts" / f"{run_date}-arxiv-daily.md"
        if path.exists():
            path.unlink()


def _hugo_config(run_date: str, hugo_output_dir: Path, runtime_config: Any) -> Any:
    topic = (
        runtime_config.get("prompt", {}).get("topic")
        or runtime_config.get("domain", {}).get("topic")
        or "research"
    )
    return OmegaConf.create(
        {
            "executor": {"target_date": run_date},
            "prompt": {"topic": topic},
            "hugo": {"output_dir": str(hugo_output_dir), "auto_push": False},
        }
    )


def _commit_and_push_hugo(hugo_output_dir: Path, generated_dates: list[str], removed_dates: list[str]) -> None:
    repo_dir = hugo_output_dir.parent if hugo_output_dir.name == "content" else hugo_output_dir
    subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "content/zh/posts", "content/en/posts"], cwd=repo_dir, check=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout
    if not status.strip():
        return
    dates = sorted(set(generated_dates + removed_dates))
    date_span = f"{dates[0]}..{dates[-1]}" if dates else "legacy dates"
    subprocess.run(["git", "commit", "-m", f"Auto: Regenerate legacy arXiv daily {date_span}"], cwd=repo_dir, check=True)
    subprocess.run(["git", "push"], cwd=repo_dir, check=True)


def _date_from_filename(path: Path) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})-arxiv-daily\.md$", path.name)
    return match.group(1) if match else None


def _lang_from_path(path: Path) -> str:
    for part in path.parts:
        if part in {"zh", "en"}:
            return part
    return "zh"


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*['\"]?([^'\"\n]+)['\"]?\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_overview(text: str) -> str:
    match = re.search(r"^>\s+\*\*(?:今日速览|Daily Overview)\*\*[:：]\s*\n(?P<body>.*?)(?=^##\s+|\Z)", text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    lines = []
    for line in match.group("body").splitlines():
        cleaned = line.removeprefix("> ").strip()
        if "**本期论文投稿处理时间范围**" in cleaned or "**arXiv submission processing window**" in cleaned:
            continue
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _extract_processing_window(text: str) -> str:
    patterns = (
        r"\*\*本期论文投稿处理时间范围\*\*[:：]\s*(.+?)(?:。|\n)",
        r"\*\*arXiv submission processing window\*\*:\s*(.+?)(?:\.|\n)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _field_text(body: str, label: str) -> str:
    match = re.search(rf"^-\s+\*\*{re.escape(label)}\*\*:\s*(.+)$", body, flags=re.MULTILINE)
    return _clean_inline(match.group(1)) if match else ""


def _field_link(body: str, label: str) -> str:
    value = _field_text(body, label)
    match = re.search(r"\((https?://[^)]+)\)", value)
    if match:
        return match.group(1)
    match = re.search(r"https?://\S+", value)
    return match.group(0).rstrip(").") if match else ""


def _score(body: str, label: str) -> float | None:
    value = _field_text(body, label).strip("` ")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _summary(body: str, label: str) -> str:
    match = re.search(rf"\*\*{re.escape(label)}\*\*:\s*(?P<summary>.*)", body, flags=re.DOTALL)
    if not match:
        return ""
    summary = re.split(r"\n---\s*$", match.group("summary").strip(), maxsplit=1)[0]
    return _clean_inline(summary.strip())


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\r", " ").strip())


def _extract_arxiv_id(value: str) -> str:
    value = value.strip()
    for prefix in ("http://arxiv.org/abs/", "https://arxiv.org/abs/", "http://arxiv.org/pdf/", "https://arxiv.org/pdf/"):
        if value.startswith(prefix):
            return value.removeprefix(prefix).rstrip("/")
    match = re.search(r"(\d{4}\.\d{4,5}v\d+)", value)
    return match.group(1) if match else ""


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate legacy Hugo daily posts into normalized capture artifacts.")
    parser.add_argument("--content-dir", required=True, help="Hugo content directory containing zh/posts and en/posts.")
    parser.add_argument("--output-dir", default="data/capture", help="Capture output directory.")
    parser.add_argument("--cutoff-date", default=DEFAULT_CUTOFF_DATE, help="Inclusive cutoff date, YYYY-MM-DD.")
    parser.add_argument("--backup-root", default=None, help="Backup directory root. Defaults to <output-dir>/backups.")
    parser.add_argument("--no-backup", action="store_true", help="Disable backup creation.")
    parser.add_argument("--no-fetch-full-text", action="store_true", help="Do not fetch arXiv HTML/PDF/source full text for accepted papers.")
    parser.add_argument("--hugo-output-dir", default=None, help="Hugo content directory to rewrite. Defaults to --content-dir.")
    parser.add_argument("--hugo-auto-push", action="store_true", help="Commit and push regenerated/deleted Hugo posts after migration.")
    parser.add_argument("--no-replace-legacy", action="store_true", help="Do not remove previous legacy migration records before writing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = migrate_legacy_hugo_capture(
        content_dir=args.content_dir,
        output_dir=args.output_dir,
        cutoff_date=args.cutoff_date,
        backup=not args.no_backup,
        backup_root=args.backup_root,
        fetch_full_text=not args.no_fetch_full_text,
        hugo_output_dir=args.hugo_output_dir,
        hugo_auto_push=args.hugo_auto_push,
        replace_legacy=not args.no_replace_legacy,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
