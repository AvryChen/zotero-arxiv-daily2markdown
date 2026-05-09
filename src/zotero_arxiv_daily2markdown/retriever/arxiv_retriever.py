from .base import BaseRetriever, register_retriever
import arxiv
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from tempfile import TemporaryDirectory
import feedparser
from tqdm import tqdm
import multiprocessing
import os
from queue import Empty
from typing import Any, Callable, TypeVar
from loguru import logger
import requests
import time
import random

T = TypeVar("T")

DOWNLOAD_TIMEOUT = (10, 60)
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180


def _download_file(url: str, path: str) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file.write(chunk)


def _run_in_subprocess(
    result_queue: Any,
    func: Callable[..., T | None],
    args: tuple[Any, ...],
) -> None:
    try:
        result_queue.put(("ok", func(*args)))
    except Exception as exc:
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _run_with_hard_timeout(
    func: Callable[..., T | None],
    args: tuple[Any, ...],
    *,
    timeout: float,
    operation: str,
    paper_title: str,
) -> T | None:
    start_methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in start_methods else start_methods[0])
    result_queue = context.Queue()
    process = context.Process(target=_run_in_subprocess, args=(result_queue, func, args))
    process.start()

    try:
        status, payload = result_queue.get(timeout=timeout)
    except Empty:
        if process.is_alive():
            process.kill()
        process.join(5)
        result_queue.close()
        result_queue.join_thread()
        logger.warning(f"{operation} timed out for {paper_title} after {timeout} seconds")
        return None

    process.join(5)
    result_queue.close()
    result_queue.join_thread()

    if status == "ok":
        return payload

    logger.warning(f"{operation} failed for {paper_title}: {payload}")
    return None


def _extract_text_from_pdf_worker(pdf_url: str) -> str:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        _download_file(pdf_url, path)
        return extract_markdown_from_pdf(path)


def _extract_text_from_html_worker(html_url: str) -> str | None:
    import trafilatura

    downloaded = trafilatura.fetch_url(html_url)
    if downloaded is None:
        raise ValueError(f"Failed to download HTML from {html_url}")
    text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    if not text:
        raise ValueError(f"No text extracted from {html_url}")
    return text


def _extract_text_from_tar_worker(source_url: str, paper_id: str) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        _download_file(source_url, path)
        file_contents = extract_tex_code_from_tar(path, paper_id)
        if not file_contents or "all" not in file_contents:
            raise ValueError("Main tex file not found.")
        return file_contents["all"]


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")

    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10, delay_seconds=20)
        query = '+'.join(self.config.source.arxiv.category)
        include_cross_list = self.config.source.arxiv.get("include_cross_list", False)
        
        target_date = self.config.executor.get("target_date")
        raw_papers = []
        
        if target_date:
            logger.info(f"Using OAI-PMH to fetch papers for target_date: {target_date}")
            import requests
            from xml.etree import ElementTree as ET
            
            def get_oai_set(cat: str) -> str:
                physics_cats = {"cond-mat", "astro-ph", "gr-qc", "hep-ex", "hep-lat", "hep-ph", "hep-th", "math-ph", "nlin", "nucl-ex", "nucl-th", "quant-ph"}
                main_cat = cat.split('.')[0]
                if main_cat in physics_cats:
                    return f"physics:{main_cat}"
                return main_cat

            all_paper_ids = []
            for cat in self.config.source.arxiv.category:
                oai_set = get_oai_set(cat)
                url = f"http://export.arxiv.org/oai2?verb=ListIdentifiers&from={target_date}&until={target_date}&metadataPrefix=arXiv&set={oai_set}"
                try:
                    response = requests.get(url, timeout=30)
                    response.raise_for_status()
                    root = ET.fromstring(response.text)
                    for record in root.findall(".//{http://www.openarchives.org/OAI/2.0/}identifier"):
                        if record.text:
                            all_paper_ids.append(record.text.replace("oai:arXiv.org:", ""))
                except Exception as e:
                    logger.warning(f"Failed to fetch OAI-PMH for {cat} on {target_date}: {e}")
            
            # Remove duplicates
            all_paper_ids = list(set(all_paper_ids))
            
        else:
            # Get the latest paper from arxiv rss feed
            all_paper_ids = []
            allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            # ArXiv RSS feeds are usually per-category. If multiple categories are provided,
            # we should fetch them individually or use the group if it's a group name.
            # However, the current query joins them with '+', which might not work for RSS.
            categories = self.config.source.arxiv.category
            if isinstance(categories, str):
                categories = [categories]
            
            for cat in categories:
                feed_url = f"https://rss.arxiv.org/atom/{cat}"
                logger.debug(f"Fetching arxiv rss feed from {feed_url}")
                try:
                    response = requests.get(feed_url, headers=headers, timeout=30)
                    response.raise_for_status()
                    feed = feedparser.parse(response.content)
                    
                    if hasattr(feed.feed, 'title') and 'Feed error for query' in feed.feed.title:
                        logger.warning(f"Invalid ARXIV_QUERY: {cat}. Skipping.")
                        continue
                    
                    cat_paper_ids = [
                        i.id.removeprefix("oai:arXiv.org:")
                        for i in feed.entries
                        if i.get("arxiv_announce_type", "new") in allowed_announce_types
                    ]
                    logger.info(f"Found {len(feed.entries)} entries for {cat}, {len(cat_paper_ids)} matched types {allowed_announce_types}")
                    all_paper_ids.extend(cat_paper_ids)
                except Exception as e:
                    logger.warning(f"Failed to fetch RSS feed for {cat}: {e}")
            
            # Remove duplicates
            all_paper_ids = list(set(all_paper_ids))
            logger.info(f"Total {len(all_paper_ids)} unique papers found from RSS feeds")

            # Fallback to search API if RSS is empty
            if not all_paper_ids:
                logger.info("RSS feed empty or failed. Falling back to Search API...")
                try:
                    import datetime
                    date_limit = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y%m%d") + "0000"
                    cat_query = ' OR '.join([f"cat:{cat}" for cat in categories])
                    search_query = f"({cat_query}) AND submittedDate:[{date_limit} TO *]"
                    search = arxiv.Search(
                        query=search_query,
                        max_results=50,
                        sort_by=arxiv.SortCriterion.SubmittedDate
                    )
                    results = list(client.results(search))
                    raw_papers.extend(results)
                    logger.info(f"Fallback search found {len(results)} latest papers for {search_query}")
                    return raw_papers
                except Exception as e:
                    logger.warning(f"Fallback search failed: {e}")

        if self.config.executor.debug:
            all_paper_ids = all_paper_ids[:10]

        # Get full information of each paper from arxiv api
        bar = tqdm(total=len(all_paper_ids))
        batch_size = 10
        for i in range(0, len(all_paper_ids), batch_size):
            if i > 0:
                # Use a random sleep between 5-10 seconds to look more human
                sleep_time = random.uniform(5.0, 10.0)
                time.sleep(sleep_time)
            
            ids = all_paper_ids[i:i + batch_size]
            try:
                search = arxiv.Search(id_list=ids)
                batch = list(client.results(search))
                bar.update(len(batch))
                raw_papers.extend(batch)
            except Exception as e:
                logger.warning(f"Failed to fetch batch {i//batch_size}: {e}. Retrying after 60s cooldown...")
                time.sleep(60)
                try:
                    search = arxiv.Search(id_list=ids)
                    batch = list(client.results(search))
                    bar.update(len(batch))
                    raw_papers.extend(batch)
                except Exception as e2:
                    logger.error(f"Failed again on batch {i//batch_size}: {e2}. Skipping this batch to avoid blocking.")
                    bar.update(len(ids))
        bar.close()

        return raw_papers

    def convert_to_paper(self, raw_paper: ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        full_text = extract_text_from_html(raw_paper)
        if full_text is None:
            full_text = extract_text_from_pdf(raw_paper)
        if full_text is None:
            full_text = extract_text_from_tar(raw_paper)
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=raw_paper.entry_id,
            pdf_url=pdf_url,
            full_text=full_text,
        )


def extract_text_from_html(paper: ArxivResult) -> str | None:
    html_url = paper.entry_id.replace("/abs/", "/html/")
    try:
        return _extract_text_from_html_worker(html_url)
    except Exception as exc:
        logger.warning(f"HTML extraction failed for {paper.title}: {exc}")
        return None


def extract_text_from_pdf(paper: ArxivResult) -> str | None:
    if paper.pdf_url is None:
        logger.warning(f"No PDF URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_pdf_worker,
        (paper.pdf_url,),
        timeout=PDF_EXTRACT_TIMEOUT,
        operation="PDF extraction",
        paper_title=paper.title,
    )


def extract_text_from_tar(paper: ArxivResult) -> str | None:
    source_url = paper.source_url()
    if source_url is None:
        logger.warning(f"No source URL available for {paper.title}")
        return None
    return _run_with_hard_timeout(
        _extract_text_from_tar_worker,
        (source_url, paper.entry_id),
        timeout=TAR_EXTRACT_TIMEOUT,
        operation="Tar extraction",
        paper_title=paper.title,
    )
