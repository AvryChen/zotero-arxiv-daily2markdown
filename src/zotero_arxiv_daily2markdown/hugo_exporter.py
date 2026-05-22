import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from loguru import logger
from omegaconf import DictConfig
from .protocol import Paper
import subprocess
import re

EMPTY_NOTICE_MARKER = "arxiv_empty_notice: true"


@dataclass
class HugoExportArtifacts:
    date_str: str
    post_date_time: str
    topic: str
    filepath_zh: str
    filepath_en: str
    content_zh: str
    content_en: str


def _resolve_hugo_date_metadata(config: DictConfig) -> tuple[str, str]:
    if hasattr(config.executor, "target_date") and config.executor.target_date:
        date_str = config.executor.target_date
        post_date_time = f"{date_str}T20:00:00+08:00"
    else:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        post_date_time = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return date_str, post_date_time


def _resolve_hugo_output_paths(config: DictConfig, date_str: str) -> tuple[str, str]:
    output_dir = config.hugo.output_dir
    filename = f"{date_str}-arxiv-daily.md"
    filepath_zh = os.path.join(output_dir, "zh", "posts", filename)
    filepath_en = os.path.join(output_dir, "en", "posts", filename)
    return filepath_zh, filepath_en


def build_hugo_export_artifacts(
    papers: list[Paper],
    config: DictConfig,
    overview_zh: str = "",
    overview_en: str = "",
) -> HugoExportArtifacts:
    date_str, post_date_time = _resolve_hugo_date_metadata(config)
    prompt_cfg = config.get("prompt", {})
    topic = prompt_cfg.get("topic", "research")
    filepath_zh, filepath_en = _resolve_hugo_output_paths(config, date_str)
    return HugoExportArtifacts(
        date_str=date_str,
        post_date_time=post_date_time,
        topic=topic,
        filepath_zh=filepath_zh,
        filepath_en=filepath_en,
        content_zh=_render_post_markdown(papers, "zh", date_str, post_date_time, topic, overview_zh),
        content_en=_render_post_markdown(papers, "en", date_str, post_date_time, topic, overview_en),
    )


def build_empty_notice_hugo_artifacts(config: DictConfig) -> HugoExportArtifacts:
    date_str, post_date_time = _resolve_hugo_date_metadata(config)
    prompt_cfg = config.get("prompt", {})
    topic = prompt_cfg.get("topic", "research")
    filepath_zh, filepath_en = _resolve_hugo_output_paths(config, date_str)
    return HugoExportArtifacts(
        date_str=date_str,
        post_date_time=post_date_time,
        topic=topic,
        filepath_zh=filepath_zh,
        filepath_en=filepath_en,
        content_zh=_render_empty_notice_markdown(
            lang="zh",
            date_str=date_str,
            post_date_time=post_date_time,
            topic=topic,
        ),
        content_en=_render_empty_notice_markdown(
            lang="en",
            date_str=date_str,
            post_date_time=post_date_time,
            topic=topic,
        ),
    )


def _render_empty_notice_markdown(*, lang: str, date_str: str, post_date_time: str, topic: str) -> str:
    is_zh = lang == "zh"
    title = "昨天没有新论文" if is_zh else f"arXiv Daily: no new papers for {date_str}"
    notice = "昨天没有新论文。" if is_zh else "No new papers yesterday."
    return "\n".join(
        [
            "---",
            f'title: "{title}"',
            f"date: {post_date_time}",
            "tags: [arxiv, paper]",
            "categories: [Daily]",
            f"lang: {lang}",
            EMPTY_NOTICE_MARKER,
            "---",
            "",
            "> **今日速览**：" if is_zh else "> **Daily Overview**:",
            f"> {notice}",
            "",
        ]
    )


def export_empty_notice_to_hugo(config: DictConfig) -> HugoExportArtifacts | None:
    if not hasattr(config, "hugo") or not config.hugo.get("output_dir"):
        return None
    artifacts = build_empty_notice_hugo_artifacts(config)
    Path(artifacts.filepath_zh).parent.mkdir(parents=True, exist_ok=True)
    Path(artifacts.filepath_en).parent.mkdir(parents=True, exist_ok=True)
    Path(artifacts.filepath_zh).write_text(artifacts.content_zh, encoding="utf-8")
    Path(artifacts.filepath_en).write_text(artifacts.content_en, encoding="utf-8")
    logger.info(f"Empty Hugo notice exported to {artifacts.filepath_zh} and {artifacts.filepath_en}")
    _auto_push_hugo_paths(
        config,
        [Path(artifacts.filepath_zh), Path(artifacts.filepath_en)],
        f"Auto: Add empty arXiv notice for {artifacts.date_str}",
    )
    return artifacts


def cleanup_empty_hugo_notices(config: DictConfig) -> list[Path]:
    if not hasattr(config, "hugo") or not config.hugo.get("output_dir"):
        return []
    output_dir = Path(str(config.hugo.output_dir))
    removed: list[Path] = []
    for lang in ("zh", "en"):
        posts_dir = output_dir / lang / "posts"
        if not posts_dir.exists():
            continue
        for path in posts_dir.glob("*-arxiv-daily.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(f"Failed to read Hugo post {path}: {exc}")
                continue
            if EMPTY_NOTICE_MARKER in text:
                path.unlink()
                removed.append(path)
    if removed:
        logger.info(f"Removed {len(removed)} stale empty Hugo notice files")
        _auto_push_hugo_paths(config, removed, "Auto: Remove stale empty arXiv notices")
    return removed


def _auto_push_hugo_paths(config: DictConfig, paths: list[Path], commit_msg: str) -> None:
    if not paths:
        return
    if not (config.hugo.get("auto_push", False) or str(os.environ.get("HUGO_AUTO_PUSH", "")).lower() in ("true", "1")):
        return
    output_dir = Path(str(config.hugo.output_dir))
    repo_dir = output_dir.parent if output_dir.name == "content" else output_dir
    try:
        if (repo_dir / ".git" / "rebase-merge").exists() or (repo_dir / ".git" / "rebase-apply").exists():
            logger.warning("Detected a failed rebase. Aborting to reach a clean state.")
            subprocess.run(["git", "rebase", "--abort"], cwd=repo_dir)
        subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs"], cwd=repo_dir)
        subprocess.run(["git", "add", "--", *(str(path) for path in paths)], cwd=repo_dir, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True).stdout
        if status:
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir, check=True)
            subprocess.run(["git", "push"], cwd=repo_dir, check=True)
    except subprocess.CalledProcessError as exc:
        logger.error(f"Git operation failed. Error: {exc}")
    except Exception as exc:
        logger.error(f"Unexpected error during git push: {exc}")


def extract_hugo_paper_urls(markdown: str) -> list[str]:
    pattern = re.compile(r"^- \*\*Link\*\*:\s+\[(?P<url>[^\]]+)\]\((?P=url)\)$", re.MULTILINE)
    return [match.group("url").strip() for match in pattern.finditer(markdown)]

BEIJING_TZ = timezone(timedelta(hours=8))


def _normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_summary(text: str, lang: str) -> str:
    text = text.strip()
    prefixes = ["TLDR", "Summary"]
    if lang == "zh":
        prefixes.extend(["总结", "摘要"])
    pattern = r"^(?:\*\*)?(?:" + "|".join(prefixes) + r")(?:\*\*)?\s*[:：]\s*"
    text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return _normalize_text(text)


def _to_utc(dt: datetime | str | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(f"Could not parse arXiv published_at timestamp: {dt}")
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_publication_window(papers: list[Paper], lang: str) -> str:
    published_times = sorted(
        dt
        for dt in (_to_utc(paper.published_at) for paper in papers)
        if dt is not None
    )
    if not published_times:
        return (
            "**本期论文投稿处理时间范围**：暂无可用时间（北京时间）。"
            if lang == "zh"
            else "**arXiv submission processing window**: times are unavailable (UTC)."
        )

    start = published_times[0]
    end = published_times[-1]

    if lang == "zh":
        beijing_start = start.astimezone(BEIJING_TZ)
        beijing_end = end.astimezone(BEIJING_TZ)
        return (
            "**本期论文投稿处理时间范围**："
            f"{beijing_start:%Y-%m-%d %H:%M} 至 {beijing_end:%Y-%m-%d %H:%M}（北京时间）。"
        )
    return (
        "**arXiv submission processing window**: "
        f"{start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC."
    )


def _render_post_markdown(
    papers: list[Paper],
    lang: str,
    date_str: str,
    post_date_time: str,
    topic: str,
    overview: str = "",
) -> str:
    is_zh = lang == "zh"
    title = (
        f"{topic} 领域 arXiv 论文日常推送 {date_str}"
        if is_zh
        else f"arXiv Daily: {topic} {date_str}"
    )
    overview_heading = "> **今日速览**：" if is_zh else "> **Daily Overview**:"
    score_label = "关联度评分" if is_zh else "Relevance Score"
    authors_label = "作者" if is_zh else "Authors"
    aff_label = "机构" if is_zh else "Affiliations"
    link_label = "链接" if is_zh else "Link"
    summary_label = "总结" if is_zh else "Summary"
    fallback_overview = (
        f"本文按照与 {topic} 领域的相关度排序论文，摘要由 AI 自动生成，仅供参考。"
        if is_zh
        else f"This post sorts papers by relevance to {topic}. Summaries are AI-generated and may contain errors."
    )

    content = [
        "---",
        f'title: "{title}"',
        f"date: {post_date_time}",
        "tags: [arxiv, paper]",
        "categories: [Daily]",
        f"lang: {lang}",
        "---",
        "",
        overview_heading,
    ]

    normalized_overview = _normalize_text(overview) or fallback_overview
    if normalized_overview:
        content.append(f"> {normalized_overview}")
    content.append(f"> {_format_publication_window(papers, lang)}")
    content.append("")

    for i, paper in enumerate(papers, 1):
        score_str = f"`{paper.score:.4f}`" if paper.score is not None else "`N/A`"
        summary = paper.tldr if is_zh else (paper.tldr_en if paper.tldr_en else paper.tldr)
        summary = _normalize_summary(summary or "", lang)

        content.append(f"## {i}. {paper.title}")
        content.append(f"- **{score_label}**: {score_str}")
        content.append(f"- **{authors_label}**: {', '.join(paper.authors)}")
        if paper.affiliations:
            content.append(f"- **{aff_label}**: {', '.join(paper.affiliations)}")
        content.append(f"- **{link_label}**: [{paper.url}]({paper.url})")
        content.append("")
        content.append(f"**{summary_label}**: {summary}")
        content.append("")
        content.append("---")
        content.append("")

    return "\n".join(content).rstrip() + "\n"

def export_to_hugo(papers: list[Paper], config: DictConfig, overview_zh: str = "", overview_en: str = ""):
    if not hasattr(config, "hugo") or not config.hugo.get("output_dir"):
        return
    artifacts = build_hugo_export_artifacts(papers, config, overview_zh=overview_zh, overview_en=overview_en)
    os.makedirs(os.path.dirname(artifacts.filepath_zh), exist_ok=True)
    os.makedirs(os.path.dirname(artifacts.filepath_en), exist_ok=True)
    
    # Auto push to Github
    if config.hugo.get("auto_push", False) or str(os.environ.get("HUGO_AUTO_PUSH", "")).lower() in ("true", "1"):
        logger.info("Starting git operations for Hugo website...")
        output_dir = config.hugo.output_dir
        repo_dir = os.path.dirname(output_dir) if os.path.basename(output_dir) == "content" else output_dir
        
        try:
            # Check if we are in a middle of a failed rebase/merge and abort it
            if os.path.exists(os.path.join(repo_dir, ".git", "rebase-merge")) or \
               os.path.exists(os.path.join(repo_dir, ".git", "rebase-apply")):
                logger.warning("Detected a failed rebase. Aborting to reach a clean state.")
                subprocess.run(["git", "rebase", "--abort"], cwd=repo_dir)

            logger.info("Pulling latest changes from remote...")
            # Use --autostash to keep any local changes safe
            subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs"], cwd=repo_dir)
        except Exception as e:
            logger.warning(f"Initial git pull failed: {e}. Proceeding anyway...")

    with open(artifacts.filepath_zh, "w", encoding="utf-8") as f:
        f.write(artifacts.content_zh)

    with open(artifacts.filepath_en, "w", encoding="utf-8") as f:
        f.write(artifacts.content_en)
        
    logger.info(f"Hugo markdown exported successfully to {artifacts.filepath_zh} and {artifacts.filepath_en}")
    
    # Commit and Push
    if config.hugo.get("auto_push", False) or str(os.environ.get("HUGO_AUTO_PUSH", "")).lower() in ("true", "1"):
        try:
            subprocess.run(["git", "add", artifacts.filepath_zh, artifacts.filepath_en], cwd=repo_dir, check=True)
            commit_msg = f"Auto: Add arXiv daily for {artifacts.date_str}"
            # Check if there are changes to commit
            status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True).stdout
            if status:
                subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir)
                logger.info("Committing changes...")
            else:
                logger.info("No changes to commit.")
            
            logger.info("Pushing to remote...")
            subprocess.run(["git", "push"], cwd=repo_dir, check=True)
            logger.info("Successfully pushed to GitHub!")
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed. Error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during git push: {e}")
