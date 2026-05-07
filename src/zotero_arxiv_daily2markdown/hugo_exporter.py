import os
from datetime import datetime
from loguru import logger
from omegaconf import DictConfig
from .protocol import Paper
import subprocess

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
    
    title_zh = f"{topic} 领域 arXiv 论文日常推送 {date_str}"
    title_en = f"arXiv Daily: {topic.capitalize()} {date_str}"
    
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

    # Generate Chinese Version
    content_zh = [
        "---",
        f'title: "{title_zh}"',
        f'date: {post_date_time}',
        "tags: [arxiv, paper]",
        "categories: [Daily]",
        "lang: zh",
        "---",
        ""
    ]
    
    if overview_zh:
        formatted_overview_zh = "\n".join([f"> {line}" for line in overview_zh.split("\n")])
        content_zh.append(f"> **今日速览**：\n{formatted_overview_zh}")
    else:
        content_zh.append(f"> **说明**：本文只是将相关领域的论文按照关联度评分排序，越靠前代表越与 {topic} 领域有关。摘要由 AI 自动生成，可能存在误差，仅供参考。")
    content_zh.append("")
    
    for i, paper in enumerate(papers, 1):
        score_str = f"{paper.score:.4f}" if paper.score is not None else "N/A"
        content_zh.append(f"## {i}. {paper.title}")
        content_zh.append(f"- **关联度评分**: `{score_str}`")
        content_zh.append(f"- **作者**: {', '.join(paper.authors)}")
        if paper.affiliations:
            content_zh.append(f"- **机构**: {', '.join(paper.affiliations)}")
        content_zh.append(f"- **链接**: [{paper.url}]({paper.url})")
        content_zh.append("")
        content_zh.append(f"**总结**: {paper.tldr}")
        content_zh.append("")
        content_zh.append("---")
        content_zh.append("")
        
    with open(filepath_zh, "w", encoding="utf-8") as f:
        f.write("\n".join(content_zh))
        
    # Generate English Version
    content_en = [
        "---",
        f'title: "{title_en}"',
        f'date: {post_date_time}',
        "tags: [arxiv, paper]",
        "categories: [Daily]",
        "lang: en",
        "---",
        ""
    ]
    
    if overview_en:
        formatted_overview_en = "\n".join([f"> {line}" for line in overview_en.split("\n")])
        content_en.append(f"> **Daily Overview**:\n{formatted_overview_en}")
    else:
        content_en.append(f"> **Note**: This post sorts papers based on relevance to {topic}. Summaries are AI-generated and may contain errors.")
    content_en.append("")
    
    for i, paper in enumerate(papers, 1):
        score_str = f"{paper.score:.4f}" if paper.score is not None else "N/A"
        content_en.append(f"## {i}. {paper.title}")
        content_en.append(f"- **Relevance Score**: `{score_str}`")
        content_en.append(f"- **Authors**: {', '.join(paper.authors)}")
        if paper.affiliations:
            content_en.append(f"- **Affiliations**: {', '.join(paper.affiliations)}")
        content_en.append(f"- **Link**: [{paper.url}]({paper.url})")
        content_en.append("")
        content_en.append(f"**Summary**: {paper.tldr_en if paper.tldr_en else paper.tldr}")
        content_en.append("")
        content_en.append("---")
        content_en.append("")
        
    with open(filepath_en, "w", encoding="utf-8") as f:
        f.write("\n".join(content_en))
        
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
