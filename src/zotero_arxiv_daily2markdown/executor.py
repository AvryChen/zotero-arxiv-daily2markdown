from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig, OmegaConf
import html
import os
import time
import traceback
from .utils import glob_match, to_bool
from .retriever import get_retriever_cls
from .protocol import CorpusPaper, Paper
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from .hugo_exporter import build_hugo_export_artifacts, extract_hugo_paper_urls, export_to_hugo
from openai import OpenAI
from tqdm import tqdm
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


@dataclass
class DailyRunResult:
    target_date: str | None
    retrieved_count: int = 0
    selected_count: int = 0
    exported: bool = False
    emailed: bool = False
    skipped: bool = False
    error: str | None = None


@dataclass
class SingleDayArtifacts:
    result: DailyRunResult
    papers: list[Paper]
    overview_zh: str
    overview_en: str


def parse_executor_date(value: str, config_key: str) -> date:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"config.executor.{config_key} must use YYYY-MM-DD format, got {value!r}") from exc


def expand_date_range(start_date: str, end_date: str) -> list[str]:
    start = parse_executor_date(start_date, "start_date")
    end = parse_executor_date(end_date, "end_date")
    if start > end:
        raise ValueError("config.executor.start_date must be earlier than or equal to end_date")
    days = (end - start).days
    return [(start + timedelta(days=offset)).isoformat() for offset in range(days + 1)]


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        
        # Normalize debug flag in config to handle string "false" from .env
        OmegaConf.set_struct(config, False)
        config.executor.debug = to_bool(config.executor.get("debug", False))
        self.debug = config.executor.debug

        unsupported_sources = [source for source in config.executor.source if source != "arxiv"]
        if unsupported_sources:
            raise ValueError(f"Only arxiv is supported as a paper source. Unsupported sources: {unsupported_sources}")

        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)

    def _set_retriever_full_text_mode(self, enabled: bool) -> None:
        for retriever in self.retrievers.values():
            if hasattr(retriever, "fetch_full_text_during_retrieval"):
                retriever.fetch_full_text_during_retrieval = enabled

    def _enrich_selected_papers(self, papers):
        for paper in tqdm(papers, desc="Fetching full text for shortlisted papers"):
            retriever = self.retrievers.get(paper.source)
            if retriever is not None and hasattr(retriever, "populate_full_text"):
                retriever.populate_full_text(paper)
        return papers

    def _generate_longlist_summaries(self, papers) -> None:
        llm_concurrency = int(self.config.executor.get("llm_concurrency", 3))
        llm_concurrency = max(1, llm_concurrency)

        def generate_for_paper(paper):
            paper.generate_tldr(self.openai_client, self.config.llm)
            paper.generate_english_tldr(self.openai_client, self.config.llm)

        if llm_concurrency == 1 or len(papers) <= 1:
            for paper in tqdm(papers, desc="Generating TLDRs for longlisted papers"):
                generate_for_paper(paper)
            return

        with ThreadPoolExecutor(max_workers=min(llm_concurrency, len(papers))) as pool:
            futures = [pool.submit(generate_for_paper, paper) for paper in papers]
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Generating TLDRs for longlisted papers",
            ):
                future.result()

    def _resolve_longlist_size(self) -> int:
        configured_longlist = self.config.executor.get("longlist")
        max_paper_num = int(self.config.executor.max_paper_num)
        if configured_longlist is None:
            return max(max_paper_num, math.ceil(max_paper_num * 1.5))
        return max(max_paper_num, int(configured_longlist))

    def _get_date_range(self) -> list[str] | None:
        start_date = self.config.executor.get("start_date")
        end_date = self.config.executor.get("end_date")
        target_date = self.config.executor.get("target_date")
        if target_date and (start_date or end_date):
            raise ValueError("executor.target_date cannot be used together with executor.start_date/end_date")
        if bool(start_date) != bool(end_date):
            raise ValueError("executor.start_date and executor.end_date must be configured together")
        if not start_date:
            return None
        return expand_date_range(str(start_date), str(end_date))

    def _historical_send_email_enabled(self) -> bool:
        mode = self.config.executor.get("historical_mode", "export_only")
        if mode == "export_only":
            return False
        if mode == "email_and_export":
            return True
        raise ValueError("executor.historical_mode must be 'export_only' or 'email_and_export'")

    def _hugo_outputs_exist(self, target_date: str) -> bool:
        if not hasattr(self.config, "hugo") or not self.config.hugo.get("output_dir"):
            return False
        output_dir = self.config.hugo.output_dir
        filename = f"{target_date}-arxiv-daily.md"
        return all(
            os.path.exists(os.path.join(output_dir, lang, "posts", filename))
            for lang in ("zh", "en")
        )

    def _error_email_enabled(self) -> bool:
        return to_bool(self.config.executor.get("error_email_enabled", True))

    def _last_fetch_report_summary(self) -> str:
        summaries = []
        for source, retriever in self.retrievers.items():
            report = getattr(retriever, "last_fetch_report", None)
            if report is not None:
                summaries.append(f"{source}: {report.summary()}")
        return "\n".join(summaries) if summaries else "No arXiv fetch report was recorded."

    def _send_error_email(self, *, stage: str, exc: Exception, target_date: str | None = None) -> None:
        if not self._error_email_enabled():
            return

        mode = target_date or self.config.executor.get("target_date") or "latest"
        configured_categories = self.config.source.arxiv.category
        if isinstance(configured_categories, str):
            categories = configured_categories
        else:
            categories = ", ".join(str(category) for category in configured_categories)
        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        report_summary = self._last_fetch_report_summary()
        body = f"""
        <html>
          <body>
            <h2>arXiv Daily failed</h2>
            <p><strong>Stage:</strong> {html.escape(stage)}</p>
            <p><strong>Target date/mode:</strong> {html.escape(str(mode))}</p>
            <p><strong>Categories:</strong> {html.escape(categories)}</p>
            <p><strong>Error type:</strong> {html.escape(type(exc).__name__)}</p>
            <p><strong>Error:</strong> {html.escape(str(exc))}</p>
            <h3>Latest fetch report</h3>
            <pre>{html.escape(report_summary)}</pre>
            <h3>Traceback</h3>
            <pre>{html.escape(trace)}</pre>
          </body>
        </html>
        """
        try:
            send_email(self.config, body, subject=f"arXiv Daily failed: {mode}")
        except Exception as email_exc:
            logger.warning(f"Failed to send error alert email: {email_exc}")

    def _build_single_day_artifacts(self, corpus: list[CorpusPaper]) -> SingleDayArtifacts:
        target_date = self.config.executor.get("target_date")
        result = DailyRunResult(target_date=str(target_date) if target_date else None)
        all_papers = []
        self._set_retriever_full_text_mode(False)

        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)

        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        result.retrieved_count = len(all_papers)

        reranked_papers: list[Paper] = []
        if len(all_papers) > 0:
            logger.info("Reranking papers using title and abstract...")
            reranked_papers = self.reranker.rerank(all_papers, corpus, include_full_text=False)
            threshold = self.config.executor.get("score_threshold", 3.0)
            reranked_papers = [p for p in reranked_papers if p.score is not None and p.score >= threshold]
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]

            if len(reranked_papers) == 0:
                logger.info(f"No papers met the score threshold of {threshold}.")
            else:
                logger.info(f"Selected {len(reranked_papers)} papers above threshold {threshold}")

            if len(reranked_papers) > 0:
                logger.info("Fetching full text and generating summaries for final selected papers...")
                self._enrich_selected_papers(reranked_papers)
                for paper in tqdm(reranked_papers):
                    paper.generate_tldr(self.openai_client, self.config.llm)
                    paper.generate_english_tldr(self.openai_client, self.config.llm)
                    paper.generate_affiliations(self.openai_client, self.config.llm)

        result.selected_count = len(reranked_papers)

        logger.info("Generating daily overview...")
        overview_zh = ""
        overview_en = ""
        high_score_papers = [p for p in reranked_papers if p.score is not None and p.score >= 3.0]
        if high_score_papers:
            papers_info = []
            for i, paper in enumerate(high_score_papers, 1):
                affil = ", ".join(paper.affiliations) if paper.affiliations else "Unknown"
                papers_info.append(
                    f"[{i}] Title: {paper.title}\nAuthors: {', '.join(paper.authors)}\nAffiliations: {affil}\nSummary: {paper.tldr}"
                )
            papers_text = "\n\n".join(papers_info)

            prompt_cfg = self.config.get("prompt", {})
            topic = prompt_cfg.get("topic", "research")
            role = prompt_cfg.get("role", "专业的学术编辑")
            overview_template = prompt_cfg.get("overview_zh", "请总结以下论文: {topic}")
            translation_prompt = prompt_cfg.get("translation_en", "Please translate:")

            prompt_zh = overview_template.format(topic=topic) + f"\n\n{papers_text}"

            try:
                response = self.openai_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": role},
                        {"role": "user", "content": prompt_zh},
                    ],
                    **self.config.llm.get("generation_kwargs", {}),
                )
                overview_zh = response.choices[0].message.content

                prompt_en = f"{translation_prompt}\n\n{overview_zh}"
                response_en = self.openai_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "You are a professional translator for academic papers."},
                        {"role": "user", "content": prompt_en},
                    ],
                    **self.config.llm.get("generation_kwargs", {}),
                )
                overview_en = response_en.choices[0].message.content
            except Exception as exc:
                logger.error(f"Failed to generate overview: {exc}")

        return SingleDayArtifacts(
            result=result,
            papers=reranked_papers,
            overview_zh=overview_zh,
            overview_en=overview_en,
        )

    def _paper_urls(self, papers: list[Paper]) -> list[str]:
        return [paper.url for paper in papers if paper.url]

    def _correction_target_date(self) -> str:
        return (datetime.now().date() - timedelta(days=1)).isoformat()

    def _hugo_output_paths_for_target_date(self, target_date: str) -> tuple[str, str]:
        if not hasattr(self.config, "hugo") or not self.config.hugo.get("output_dir"):
            return "", ""

        original_target_date = self.config.executor.get("target_date")
        try:
            self.config.executor.target_date = target_date
            artifacts = build_hugo_export_artifacts([], self.config)
            return artifacts.filepath_zh, artifacts.filepath_en
        finally:
            self.config.executor.target_date = original_target_date

    def _read_hugo_urls(self, path: str) -> list[str]:
        if not path or not os.path.exists(path):
            return []
        try:
            return extract_hugo_paper_urls(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning(f"Failed to read Hugo output {path}: {exc}")
            return []

    def _hugo_output_urls_match(self, target_date: str, papers: list[Paper]) -> bool:
        filepath_zh, filepath_en = self._hugo_output_paths_for_target_date(target_date)
        if not filepath_zh or not filepath_en:
            return False
        if not os.path.exists(filepath_zh) or not os.path.exists(filepath_en):
            return False

        expected_urls = set(self._paper_urls(papers))
        existing_zh = set(self._read_hugo_urls(filepath_zh))
        existing_en = set(self._read_hugo_urls(filepath_en))
        return expected_urls == existing_zh == existing_en

    def _send_revision_email(self, target_date: str, papers: list[Paper]) -> None:
        note = f"昨日修订：{target_date}"
        logger.info(f"Sending revision email for {target_date}")
        try:
            email_content = render_email(papers, revision_note=note)
            send_email(self.config, email_content, subject=f"arXiv Daily revision: {target_date}")
        except Exception as exc:
            logger.warning(f"Revision email failed for {target_date}; continuing without email: {exc}")

    def _apply_previous_day_correction(self, corpus: list[CorpusPaper] | None = None) -> None:
        if not hasattr(self.config, "hugo") or not self.config.hugo.get("output_dir"):
            logger.info("Skipping previous-day correction because Hugo output is not configured.")
            return

        corpus = corpus or []
        target_date = self._correction_target_date()
        logger.info(f"Running previous-day correction for {target_date}")
        original_target_date = self.config.executor.get("target_date")
        try:
            self.config.executor.target_date = target_date
            artifacts = self._build_single_day_artifacts(corpus)
            if self._hugo_output_urls_match(target_date, artifacts.papers):
                logger.info(f"No Hugo correction needed for {target_date}: paper URLs already match.")
                return

            export_to_hugo(artifacts.papers, self.config, artifacts.overview_zh, artifacts.overview_en)
            self._send_revision_email(target_date, artifacts.papers)
            logger.info(f"Previous-day correction completed for {target_date}")
        except Exception as exc:
            self._send_error_email(stage="previous-day correction", exc=exc, target_date=target_date)
            logger.exception(f"Previous-day correction failed for {target_date}")
        finally:
            self.config.executor.target_date = original_target_date

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = random.sample(corpus, min(5, len(corpus)))
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    def _run_single_day(self, corpus: list[CorpusPaper], *, send_email_enabled: bool = True) -> DailyRunResult:
        try:
            return self._run_single_day_impl(corpus, send_email_enabled=send_email_enabled)
        except Exception as exc:
            self._send_error_email(stage="single-day run", exc=exc)
            raise

    def _run_single_day_impl(self, corpus: list[CorpusPaper], *, send_email_enabled: bool = True) -> DailyRunResult:
        artifacts = self._build_single_day_artifacts(corpus)
        result = artifacts.result
        if result.retrieved_count == 0 and not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            result.skipped = True
            return result

        if result.retrieved_count > 0 and result.selected_count == 0 and not self.config.executor.send_empty:
            logger.info("No papers met the score threshold. No email will be sent.")
            result.skipped = True
            return result

        if send_email_enabled:
            logger.info("Sending email...")
            try:
                email_content = render_email(artifacts.papers)
                send_email(self.config, email_content)
                result.emailed = True
                logger.info("Email sent successfully")
            except Exception as exc:
                logger.warning(f"Email failed; continuing without email: {exc}")
        else:
            logger.info("Skipping email for this run.")

        export_to_hugo(artifacts.papers, self.config, artifacts.overview_zh, artifacts.overview_en)
        result.exported = True
        return result

    def _run_date_range(self, dates: list[str], corpus: list[CorpusPaper]) -> list[DailyRunResult]:
        send_email_enabled = self._historical_send_email_enabled()
        continue_on_error = to_bool(self.config.executor.get("continue_on_error", False))
        skip_existing = to_bool(self.config.executor.get("skip_existing", False))
        original_target_date = self.config.executor.get("target_date")
        results = []

        try:
            for index, target_date in enumerate(dates):
                logger.info(f"Running historical arXiv daily for {target_date}")
                self.config.executor.target_date = target_date
                processed_date = False
                if skip_existing and self._hugo_outputs_exist(target_date):
                    logger.info(f"Skipping {target_date}: Hugo output already exists.")
                    results.append(DailyRunResult(target_date=target_date, skipped=True))
                    continue
                try:
                    results.append(self._run_single_day(corpus, send_email_enabled=send_email_enabled))
                    processed_date = True
                except Exception as exc:
                    logger.exception(f"Failed to run arXiv daily for {target_date}")
                    results.append(DailyRunResult(target_date=target_date, skipped=True, error=str(exc)))
                    if not continue_on_error:
                        raise
                    processed_date = True
                if processed_date and index < len(dates) - 1:
                    cooldown_seconds = max(0.0, float(self.config.executor.get("historical_day_cooldown_seconds", 600)))
                    if cooldown_seconds > 0:
                        logger.info(f"Cooling down for {cooldown_seconds:.1f}s before the next historical date")
                        time.sleep(cooldown_seconds)
        finally:
            self.config.executor.target_date = original_target_date

        succeeded = sum(1 for result in results if result.error is None and not result.skipped)
        skipped = sum(1 for result in results if result.skipped)
        failed = sum(1 for result in results if result.error is not None)
        logger.info(
            f"Historical arXiv daily finished: dates={len(results)}, "
            f"succeeded={succeeded}, skipped={skipped}, failed={failed}"
        )
        return results

    def run(self):
        dates = self._get_date_range()
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return

        if dates is not None:
            logger.info(f"Running historical arXiv daily from {dates[0]} to {dates[-1]}")
            return self._run_date_range(dates, corpus)

        result = self._run_single_day(corpus, send_email_enabled=True)
        self._apply_previous_day_correction(corpus)
        return result
