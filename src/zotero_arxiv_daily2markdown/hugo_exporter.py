import os
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Iterable
from loguru import logger
from omegaconf import DictConfig
from .protocol import Paper
import subprocess
import re

EMPTY_NOTICE_MARKER = "arxiv_empty_notice: true"
HUGO_KNOWLEDGE_INPUT_FILES = [
    "papers.jsonl",
    "paper_insights.json",
    "paper_workflows.json",
    "aligned_vocabulary.json",
]
HUGO_GENERATED_CONTENT_PATHS = [
    "content/en/daily",
    "content/en/papers",
    "content/en/knowledge",
    "content/zh/daily",
    "content/zh/papers",
    "content/zh/knowledge",
]


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
    next_day = _next_day_label(date_str)
    body = (
        _empty_notice_body_zh(date_str=date_str, next_day=next_day, topic=topic)
        if is_zh
        else _empty_notice_body_en(date_str=date_str, next_day=next_day, topic=topic)
    )
    body = _compact_raw_html(body)
    return "\n".join(
        [
            "---",
            f'title: "{title}"',
            f"date: {post_date_time}",
            "tags: [arxiv, paper]",
            "categories: [Daily]",
            EMPTY_NOTICE_MARKER,
            "---",
            "",
            _empty_notice_styles(),
            body,
        ]
    )


def _next_day_label(date_str: str) -> str:
    try:
        return (datetime.fromisoformat(date_str).date() + timedelta(days=1)).isoformat()
    except ValueError:
        return "the next day"


def _compact_raw_html(html: str) -> str:
    return "\n".join(line.strip() for line in html.splitlines() if line.strip())


def _empty_notice_styles() -> str:
    return """<style>
.arxiv-empty-notice {
  margin: 1.5rem 0 2.5rem;
  color: var(--primary, #1d1d1f);
}
.arxiv-empty-notice .notice-hero {
  border: 1px solid var(--border, rgba(120, 120, 120, .24));
  border-radius: 8px;
  background: var(--entry, var(--theme, #fff));
  padding: clamp(1.25rem, 3vw, 2rem);
}
.arxiv-empty-notice h2 {
  margin: 0 0 .65rem;
  font-size: clamp(1.45rem, 3vw, 2.15rem);
  line-height: 1.18;
  letter-spacing: 0;
}
.arxiv-empty-notice p {
  margin: 0;
  color: var(--secondary, #5f6368);
  line-height: 1.75;
}
.arxiv-empty-notice .notice-date {
  display: inline-flex;
  align-items: center;
  gap: .45rem;
  margin-bottom: .9rem;
  font-size: .86rem;
  color: var(--secondary, #5f6368);
}
.arxiv-empty-notice .notice-date::before {
  content: "";
  width: .62rem;
  height: .62rem;
  border-radius: 50%;
  background: #3f7f8f;
  box-shadow: 0 0 0 3px rgba(63, 127, 143, .18);
}
.arxiv-empty-notice .notice-sections {
  display: grid;
  gap: 1rem;
  margin-top: 1.25rem;
}
.arxiv-empty-notice .notice-section {
  border-top: 1px solid var(--border, rgba(120, 120, 120, .24));
  padding-top: 1rem;
}
.arxiv-empty-notice h3 {
  margin: 0 0 .45rem;
  font-size: 1rem;
  letter-spacing: 0;
}
.arxiv-empty-notice ol,
.arxiv-empty-notice ul {
  margin: .4rem 0 0 1.25rem;
  padding: 0;
  color: var(--secondary, #5f6368);
  line-height: 1.7;
}
.arxiv-empty-notice li + li {
  margin-top: .35rem;
}
.arxiv-empty-notice code {
  font-size: .88em;
}
@media (max-width: 640px) {
  .arxiv-empty-notice .notice-hero {
    padding: 1.1rem;
  }
}
</style>"""


def _empty_notice_body_zh(*, date_str: str, next_day: str, topic: str) -> str:
    return f"""<section class="arxiv-empty-notice" aria-labelledby="empty-notice-title">
  <div class="notice-hero">
    <div class="notice-date">公告日：{date_str}；通常在北京时间 {next_day} 生成</div>
    <h2 id="empty-notice-title">昨天没有新的命中论文</h2>
    <p>这不是说 arXiv 昨天没有论文，而是说在本站关注的 <strong>{topic}</strong> 主题下，昨天公告的候选论文经过相似度排序和领域判定后，没有达到收录标准。</p>
  </div>

  <div class="notice-sections">
    <section class="notice-section">
      <h3>为什么今天看到的是昨天的论文？</h3>
      <p>arXiv 的每日列表按“公告日”组织，当天页面在处理过程中可能还不完整。本站选择在北京时间每天处理前一天的公告日，这样候选列表已经收束，结果更稳定，也避免把仍在变化的当天列表提前展示给读者。</p>
    </section>

    <section class="notice-section">
      <h3>抓取与筛选流程</h3>
      <ol>
        <li>读取目标公告日的 arXiv catchup 页面，纳入 <code>New submissions</code>、<code>Cross-lists</code> 和 <code>Replacements</code>。</li>
        <li>解析论文编号、标题、作者、摘要、分类和 PDF 链接，先不下载全文。</li>
        <li>用标题和摘要与本领域参考语料计算相似度，形成 longlist。</li>
        <li>让 AI 领域判定器复核 longlist，只接受明确属于 <strong>{topic}</strong> 且置信度达标的论文。</li>
        <li>只有命中的论文才抓取 HTML、PDF 或 TeX 源文件，并生成 TXT、元数据和网页摘要。</li>
      </ol>
    </section>

    <section class="notice-section">
      <h3>今天为什么没有论文条目？</h3>
      <p>可能是昨天没有相关候选，也可能是有候选但相关度不足、领域判定为 reject/uncertain，或置信度没有达到阈值。这些论文不会进入日报正文，以免把弱相关内容推给读者。</p>
    </section>
  </div>
</section>"""


def _empty_notice_body_en(*, date_str: str, next_day: str, topic: str) -> str:
    return f"""<section class="arxiv-empty-notice" aria-labelledby="empty-notice-title">
  <div class="notice-hero">
    <div class="notice-date">Announcement date: {date_str}; usually generated on {next_day} Beijing time</div>
    <h2 id="empty-notice-title">No newly matched papers yesterday</h2>
    <p>This does not mean arXiv had no papers yesterday. It means the papers announced yesterday did not produce an accepted match for this site's <strong>{topic}</strong> scope after relevance ranking and domain review.</p>
  </div>

  <div class="notice-sections">
    <section class="notice-section">
      <h3>Why does today's update process yesterday's papers?</h3>
      <p>arXiv daily lists are organized by announcement date, and the current day's page can still change while submissions are being processed. This site processes the previous announcement date each day in Beijing time so the candidate list is closed, stable, and less likely to show partial results.</p>
    </section>

    <section class="notice-section">
      <h3>How papers are collected and filtered</h3>
      <ol>
        <li>Read the arXiv catchup page for the target announcement date, including <code>New submissions</code>, <code>Cross-lists</code>, and <code>Replacements</code>.</li>
        <li>Parse arXiv IDs, titles, authors, abstracts, categories, and PDF links without downloading full text first.</li>
        <li>Rank candidates by title and abstract similarity against the reference corpus for this research area.</li>
        <li>Ask the AI domain classifier to review the longlist, accepting only papers clearly in scope for <strong>{topic}</strong> with sufficient confidence.</li>
        <li>Fetch HTML, PDF, or TeX source only for accepted papers, then create TXT files, metadata, and web summaries.</li>
      </ol>
    </section>

    <section class="notice-section">
      <h3>Why are there no paper entries today?</h3>
      <p>There may have been no relevant candidates, or there may have been candidates whose similarity score, domain decision, or confidence was not strong enough. Those papers are left out of the public daily post to keep the feed focused.</p>
    </section>
  </div>
</section>"""


def export_empty_notice_to_hugo(
    config: DictConfig,
    extra_paths: list[str | Path] | None = None,
) -> HugoExportArtifacts | None:
    if not hasattr(config, "hugo") or not config.hugo.get("output_dir"):
        return None
    artifacts = build_empty_notice_hugo_artifacts(config)
    Path(artifacts.filepath_zh).parent.mkdir(parents=True, exist_ok=True)
    Path(artifacts.filepath_en).parent.mkdir(parents=True, exist_ok=True)
    Path(artifacts.filepath_zh).write_text(artifacts.content_zh, encoding="utf-8")
    Path(artifacts.filepath_en).write_text(artifacts.content_en, encoding="utf-8")
    logger.info(f"Empty Hugo notice exported to {artifacts.filepath_zh} and {artifacts.filepath_en}")
    paths = [Path(artifacts.filepath_zh), Path(artifacts.filepath_en)]
    if extra_paths:
        paths.extend(Path(path) for path in extra_paths)
    _auto_push_hugo_paths(
        config,
        paths,
        f"Auto: Add empty arXiv notice for {artifacts.date_str}",
        build_knowledge_pages=False,
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
        daily_dir = output_dir / lang / "daily"
        if not daily_dir.exists():
            continue
        for path in daily_dir.glob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(f"Failed to read generated Hugo daily page {path}: {exc}")
                continue
            if 'class="arxiv-empty-notice"' in text:
                path.unlink()
                removed.append(path)
    if removed:
        logger.info(f"Removed {len(removed)} stale empty Hugo notice files")
        _auto_push_hugo_paths(config, removed, "Auto: Remove stale empty arXiv notices")
    return removed


def _is_truthy(value) -> bool:
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


def _hugo_auto_push_enabled(config: DictConfig) -> bool:
    configured = config.hugo.get("auto_push", None)
    if configured is not None:
        return _is_truthy(configured)
    return _is_truthy(os.environ.get("HUGO_AUTO_PUSH", ""))


def _resolve_hugo_repo_dir(config: DictConfig) -> Path:
    output_dir = Path(str(config.hugo.output_dir))
    return output_dir.parent if output_dir.name == "content" else output_dir


def _abort_interrupted_rebase(repo_dir: Path) -> None:
    if (repo_dir / ".git" / "rebase-merge").exists() or (repo_dir / ".git" / "rebase-apply").exists():
        logger.warning("Detected a failed rebase. Aborting to reach a clean state.")
        subprocess.run(["git", "rebase", "--abort"], cwd=repo_dir)


def _pull_hugo_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "pull", "--rebase", "--autostash", "-X", "theirs"], cwd=repo_dir)


def _prepare_hugo_repo_for_write(config: DictConfig) -> Path | None:
    if not _hugo_auto_push_enabled(config):
        return None
    repo_dir = _resolve_hugo_repo_dir(config)
    try:
        logger.info("Starting git operations for Hugo website...")
        _abort_interrupted_rebase(repo_dir)
        logger.info("Pulling latest changes from remote...")
        _pull_hugo_repo(repo_dir)
    except Exception as exc:
        logger.warning(f"Initial git pull failed: {exc}. Proceeding anyway...")
    return repo_dir


def _get_config_section(config: DictConfig, section: str):
    return config.get(section, {}) if hasattr(config, "get") else {}


def _hugo_knowledge_pages_enabled(config: DictConfig) -> bool:
    hugo_config = _get_config_section(config, "hugo")
    knowledge_config = _get_config_section(config, "knowledge")
    return _is_truthy(hugo_config.get("build_knowledge_pages", True)) and _is_truthy(
        knowledge_config.get("enabled", False)
    )


def _resolve_knowledge_data_dir(config: DictConfig, repo_dir: Path) -> Path:
    hugo_config = _get_config_section(config, "hugo")
    knowledge_config = _get_config_section(config, "knowledge")
    configured = hugo_config.get("knowledge_data_dir") or knowledge_config.get("output_dir")
    if configured:
        path = Path(str(configured)).expanduser()
        return path if path.is_absolute() else repo_dir / path
    return repo_dir / "data" / "knowledge"


def _resolve_generated_content_paths(config: DictConfig, repo_dir: Path) -> list[Path]:
    hugo_config = _get_config_section(config, "hugo")
    configured = hugo_config.get("generated_content_paths") or HUGO_GENERATED_CONTENT_PATHS
    return [
        path if path.is_absolute() else repo_dir / path
        for path in (Path(str(item)) for item in configured)
    ]


def _dedupe_paths(paths: Iterable[Path | str]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        path_obj = Path(path)
        key = str(path_obj)
        if key not in seen:
            deduped.append(path_obj)
            seen.add(key)
    return deduped


def _paths_include_fresh_knowledge_output(config: DictConfig, paths: Iterable[Path | str], repo_dir: Path) -> bool:
    if not _hugo_knowledge_pages_enabled(config):
        return False
    knowledge_data_dir = _resolve_knowledge_data_dir(config, repo_dir).resolve()
    required_paths = {str((knowledge_data_dir / name).resolve()) for name in HUGO_KNOWLEDGE_INPUT_FILES}
    present_paths: set[str] = set()
    for path in paths:
        path_obj = Path(path)
        if not path_obj.is_absolute():
            path_obj = repo_dir / path_obj
        try:
            resolved = path_obj.resolve()
        except (OSError, ValueError):
            continue
        if str(resolved) in required_paths:
            present_paths.add(str(resolved))
    if present_paths != required_paths:
        return False
    return _knowledge_package_has_publishable_records(knowledge_data_dir)


def _knowledge_package_ready_for_hugo_build(config: DictConfig, repo_dir: Path) -> bool:
    if not _hugo_knowledge_pages_enabled(config):
        return False
    if not (repo_dir / "package.json").exists():
        logger.warning(f"Skipping knowledge page build because {repo_dir / 'package.json'} does not exist")
        return False
    knowledge_data_dir = _resolve_knowledge_data_dir(config, repo_dir)
    if not knowledge_data_dir.exists():
        logger.warning(f"Skipping knowledge page build because knowledge data dir does not exist: {knowledge_data_dir}")
        return False
    missing_inputs = _missing_hugo_knowledge_input_files(knowledge_data_dir)
    if missing_inputs:
        logger.warning(
            "Skipping knowledge page build because the knowledge data dir is missing files required by "
            "scripts/build_knowledge_demo.mjs: "
            + ", ".join(missing_inputs)
        )
        return False
    return _knowledge_package_has_publishable_records(knowledge_data_dir)


def _missing_hugo_knowledge_input_files(knowledge_data_dir: Path) -> list[str]:
    return [name for name in HUGO_KNOWLEDGE_INPUT_FILES if not (knowledge_data_dir / name).exists()]


def _knowledge_package_has_publishable_records(knowledge_data_dir: Path) -> bool:
    papers_jsonl = knowledge_data_dir / "papers.jsonl"
    try:
        for line_number, line in enumerate(papers_jsonl.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            json.loads(stripped)
            return True
    except json.JSONDecodeError as exc:
        logger.warning(f"Skipping knowledge page build because {papers_jsonl}:{line_number} is invalid JSONL: {exc}")
        return False
    except OSError as exc:
        logger.warning(f"Skipping knowledge page build because {papers_jsonl} is unreadable: {exc}")
        return False
    logger.warning(f"Skipping knowledge page build because {papers_jsonl} contains no paper records")
    return False


def _run_hugo_shell_command(command: str, repo_dir: Path, *, env_overrides: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    subprocess.run(command, cwd=repo_dir, shell=True, env=env, check=True)


def _build_knowledge_pages_for_hugo(config: DictConfig, repo_dir: Path) -> list[Path]:
    if not _hugo_knowledge_pages_enabled(config):
        return []
    if not (repo_dir / "package.json").exists():
        logger.warning(f"Skipping knowledge page build because {repo_dir / 'package.json'} does not exist")
        return []

    knowledge_data_dir = _resolve_knowledge_data_dir(config, repo_dir)
    if not knowledge_data_dir.exists():
        raise FileNotFoundError(
            f"Knowledge data dir does not exist: {knowledge_data_dir}. "
            "Run the incremental knowledge update before publishing Hugo."
        )
    missing_inputs = _missing_hugo_knowledge_input_files(knowledge_data_dir)
    if missing_inputs:
        raise FileNotFoundError(
            "Knowledge data dir is missing files required by scripts/build_knowledge_demo.mjs: "
            + ", ".join(missing_inputs)
        )
    if not _knowledge_package_has_publishable_records(knowledge_data_dir):
        return []

    hugo_config = _get_config_section(config, "hugo")
    knowledge_build_command = str(hugo_config.get("knowledge_build_command", "npm run knowledge:demo"))
    site_build_command = str(hugo_config.get("site_build_command", "npm run build"))

    logger.info(f"Generating knowledge pages from {knowledge_data_dir}")
    _run_hugo_shell_command(
        knowledge_build_command,
        repo_dir,
        env_overrides={"KNOWLEDGE_DATA_DIR": str(knowledge_data_dir)},
    )
    logger.info("Validating Hugo site build")
    _run_hugo_shell_command(site_build_command, repo_dir)
    return _resolve_generated_content_paths(config, repo_dir)


def _auto_push_hugo_paths(
    config: DictConfig,
    paths: list[Path],
    commit_msg: str,
    *,
    build_knowledge_pages: bool = False,
    pull_first: bool = True,
) -> None:
    if not paths:
        return
    if not _hugo_auto_push_enabled(config):
        return
    repo_dir = _resolve_hugo_repo_dir(config)
    try:
        if pull_first:
            _abort_interrupted_rebase(repo_dir)
            _pull_hugo_repo(repo_dir)
        all_paths = list(paths)
        if build_knowledge_pages and (
            _paths_include_fresh_knowledge_output(config, all_paths, repo_dir)
            or _knowledge_package_ready_for_hugo_build(config, repo_dir)
        ):
            all_paths.extend(_build_knowledge_pages_for_hugo(config, repo_dir))
        elif build_knowledge_pages and _hugo_knowledge_pages_enabled(config):
            logger.info("Skipping knowledge page build because no publishable knowledge package is available")
        subprocess.run(["git", "add", "--", *(str(path) for path in _dedupe_paths(all_paths))], cwd=repo_dir, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True).stdout
        if status:
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_dir, check=True)
            subprocess.run(["git", "push"], cwd=repo_dir, check=True)
            logger.info("Successfully pushed to GitHub!")
        else:
            logger.info("No changes to commit.")
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

def export_to_hugo(
    papers: list[Paper],
    config: DictConfig,
    overview_zh: str = "",
    overview_en: str = "",
    extra_paths: list[str | Path] | None = None,
):
    if not hasattr(config, "hugo") or not config.hugo.get("output_dir"):
        return
    _prepare_hugo_repo_for_write(config)
    artifacts = build_hugo_export_artifacts(papers, config, overview_zh=overview_zh, overview_en=overview_en)
    os.makedirs(os.path.dirname(artifacts.filepath_zh), exist_ok=True)
    os.makedirs(os.path.dirname(artifacts.filepath_en), exist_ok=True)

    with open(artifacts.filepath_zh, "w", encoding="utf-8") as f:
        f.write(artifacts.content_zh)

    with open(artifacts.filepath_en, "w", encoding="utf-8") as f:
        f.write(artifacts.content_en)
        
    logger.info(f"Hugo markdown exported successfully to {artifacts.filepath_zh} and {artifacts.filepath_en}")

    paths_to_add = [Path(artifacts.filepath_zh), Path(artifacts.filepath_en)]
    if extra_paths:
        paths_to_add.extend(Path(path) for path in extra_paths)
    _auto_push_hugo_paths(
        config,
        paths_to_add,
        f"Auto: Add arXiv daily for {artifacts.date_str}",
        build_knowledge_pages=True,
        pull_first=False,
    )
