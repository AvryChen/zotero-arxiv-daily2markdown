import os
from datetime import datetime, timedelta, timezone
from loguru import logger
from omegaconf import DictConfig
from .protocol import Paper
import subprocess
import re

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


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
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
        
    output_dir = config.hugo.output_dir
    os.makedirs(os.path.join(output_dir, "zh", "posts"), exist_ok=True)
    os.makedirs(os.path.join(output_dir, "en", "posts"), exist_ok=True)
    
    if hasattr(config.executor, "target_date") and config.executor.target_date:
        date_str = config.executor.target_date
        post_date_time = f"{date_str}T20:00:00+08:00"
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
        post_date_time = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
        
    filename = f"{date_str}-arxiv-daily.md"
    filepath_zh = os.path.join(output_dir, "zh", "posts", filename)
    filepath_en = os.path.join(output_dir, "en", "posts", filename)
    
    prompt_cfg = config.get("prompt", {})
    topic = prompt_cfg.get("topic", "research")
    
    # Auto push to Github
    if config.hugo.get("auto_push", False) or str(os.environ.get("HUGO_AUTO_PUSH", "")).lower() in ("true", "1"):
        logger.info("Starting git operations for Hugo website...")
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

    with open(filepath_zh, "w", encoding="utf-8") as f:
        f.write(_render_post_markdown(papers, "zh", date_str, post_date_time, topic, overview_zh))

    with open(filepath_en, "w", encoding="utf-8") as f:
        f.write(_render_post_markdown(papers, "en", date_str, post_date_time, topic, overview_en))
        
    logger.info(f"Hugo markdown exported successfully to {filepath_zh} and {filepath_en}")
    
    # Commit and Push
    if config.hugo.get("auto_push", False) or str(os.environ.get("HUGO_AUTO_PUSH", "")).lower() in ("true", "1"):
        try:
            subprocess.run(["git", "add", filepath_zh, filepath_en], cwd=repo_dir, check=True)
            commit_msg = f"Auto: Add arXiv daily for {date_str}"
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
