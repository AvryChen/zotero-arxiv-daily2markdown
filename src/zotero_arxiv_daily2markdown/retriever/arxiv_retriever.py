from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, TypeVar
from queue import Empty
from xml.etree import ElementTree as ET
import multiprocessing
import os
import re
import time

import arxiv
from arxiv import Result as ArxivResult
import feedparser
from loguru import logger
import requests
from tqdm import tqdm

from .base import BaseRetriever, register_retriever
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar, to_bool


T = TypeVar("T")

ARXIV_API_URL = "https://export.arxiv.org/api/query"
DAILY_ARXIV_URL = "https://dailyarxiv.com/query.php"
DOWNLOAD_TIMEOUT = (10, 60)
FETCH_TIMEOUT = 30
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
OPENSEARCH_NS = "{http://a9.com/-/spec/opensearch/1.1/}"


class ArxivFetchIntegrityError(RuntimeError):
    pass


@dataclass
class ArxivFetchReport:
    mode: str
    expected_count: int = 0
    fetched_count: int = 0
    dailyarxiv_count: int | None = None
    feed_ids: list[str] = field(default_factory=list)
    fetched_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    extra_ids: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    failed_pages: list[str] = field(default_factory=list)
    failed_batches: list[str] = field(default_factory=list)
    cross_validation_missing_ids: list[str] = field(default_factory=list)
    cross_validation_extra_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def has_primary_failures(self) -> bool:
        return bool(self.missing_ids or self.failed_pages or self.failed_batches)

    def has_cross_validation_failures(self) -> bool:
        return bool(self.cross_validation_missing_ids or self.cross_validation_extra_ids)

    def summary(self) -> str:
        dailyarxiv_count = "n/a" if self.dailyarxiv_count is None else self.dailyarxiv_count
        return (
            "source=arxiv "
            f"mode={self.mode} "
            f"expected={self.expected_count} "
            f"fetched={self.fetched_count} "
            f"dailyarxiv={dailyarxiv_count} "
            f"missing={len(self.missing_ids) + len(self.cross_validation_missing_ids)} "
            f"extra={len(self.extra_ids) + len(self.cross_validation_extra_ids)} "
            f"duplicates={len(self.duplicate_ids)}"
        )


@dataclass
class RawArxivAuthor:
    name: str


@dataclass
class RawArxivResult:
    title: str
    authors: list[RawArxivAuthor]
    summary: str
    pdf_url: str | None
    entry_id: str
    published: str | None = None
    updated: str | None = None
    categories: list[str] = field(default_factory=list)
    primary_category: str | None = None

    def source_url(self) -> str:
        return f"https://arxiv.org/e-print/{extract_arxiv_id(self.entry_id)}"


def extract_arxiv_id(value: str) -> str:
    value = value.strip()
    for prefix in (
        "oai:arXiv.org:",
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://export.arxiv.org/abs/",
        "http://export.arxiv.org/abs/",
    ):
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    return value.rstrip("/")


def stable_unique(values: list[str]) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    duplicates: list[str] = []
    unique: list[str] = []
    for value in values:
        if value in seen:
            if value not in duplicates:
                duplicates.append(value)
            continue
        seen.add(value)
        unique.append(value)
    return unique, duplicates


def parse_target_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"executor.target_date must use YYYY-MM-DD format, got {value!r}") from exc


def build_announcement_window(target_date: str) -> tuple[str, str]:
    current = parse_target_date(target_date)
    previous = current - timedelta(days=1)
    return f"{previous:%Y%m%d}2000", f"{current:%Y%m%d}1959"


def category_to_api_term(category: str) -> str:
    category = str(category).strip()
    if category.startswith("cat:"):
        return category
    if category.endswith("*"):
        return f"cat:{category}"
    if "." not in category and "-" in category:
        return f"cat:{category}*"
    return f"cat:{category}"


def category_to_dailyarxiv_term(category: str) -> str:
    category = str(category).strip()
    if category.startswith("cat:"):
        category = category.removeprefix("cat:")
    if "." not in category and "-" in category and not category.endswith("*"):
        return f"{category}*"
    return category


def get_config_bool(config: Any, key: str, default: bool = False) -> bool:
    return to_bool(config.get(key, default))


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


def _entry_text(entry: ET.Element, tag: str) -> str:
    value = entry.findtext(f"atom:{tag}", default="", namespaces=ATOM_NS)
    return re.sub(r"\s+", " ", value).strip()


def _parse_atom_feed(xml_text: str) -> tuple[list[RawArxivResult], int]:
    root = ET.fromstring(xml_text)
    total_text = root.findtext(f"{OPENSEARCH_NS}totalResults")
    total_results = int(total_text) if total_text and total_text.isdigit() else 0

    entries: list[RawArxivResult] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = _entry_text(entry, "id")
        title = _entry_text(entry, "title")
        summary = _entry_text(entry, "summary")
        if entry_id.endswith("/api/errors") or title.lower() == "error":
            raise ArxivFetchIntegrityError(summary or "arXiv API returned an error entry")

        authors = [
            RawArxivAuthor(name=re.sub(r"\s+", " ", author.findtext("atom:name", default="", namespaces=ATOM_NS)).strip())
            for author in entry.findall("atom:author", ATOM_NS)
        ]
        authors = [author for author in authors if author.name]

        pdf_url = None
        for link in entry.findall("atom:link", ATOM_NS):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href")
                break

        categories = [category.attrib["term"] for category in entry.findall("atom:category", ATOM_NS) if "term" in category.attrib]
        primary_category = None
        for element in entry:
            if element.tag == "{http://arxiv.org/schemas/atom}primary_category":
                primary_category = element.attrib.get("term")
                break

        entries.append(
            RawArxivResult(
                title=title,
                authors=authors,
                summary=summary,
                pdf_url=pdf_url,
                entry_id=entry_id,
                published=_entry_text(entry, "published") or None,
                updated=_entry_text(entry, "updated") or None,
                categories=categories,
                primary_category=primary_category,
            )
        )

    return entries, total_results


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
        self.last_fetch_report: ArxivFetchReport | None = None
        self.fetch_full_text_during_retrieval = True

    @property
    def categories(self) -> list[str]:
        categories = self.config.source.arxiv.category
        if isinstance(categories, str):
            return [categories]
        return list(categories)

    def _retrieve_raw_papers(self) -> list[ArxivResult | RawArxivResult]:
        target_date = self.config.executor.get("target_date")
        if target_date:
            raw_papers = self._retrieve_by_target_date(str(target_date))
        else:
            raw_papers = self._retrieve_latest_from_rss()

        if self.config.executor.debug:
            raw_papers = raw_papers[:10]
        return raw_papers

    def _retrieve_latest_from_rss(self) -> list[ArxivResult]:
        report = ArxivFetchReport(mode="rss")
        include_cross_list = get_config_bool(self.config.source.arxiv, "include_cross_list", False)
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
        }

        all_paper_ids: list[str] = []
        for category in self.categories:
            feed_url = f"https://rss.arxiv.org/atom/{category}"
            logger.debug(f"Fetching arxiv rss feed from {feed_url}")
            try:
                response = requests.get(feed_url, headers=headers, timeout=FETCH_TIMEOUT)
                response.raise_for_status()
                feed = feedparser.parse(response.content)
            except Exception as exc:
                report.failed_pages.append(feed_url)
                logger.warning(f"Failed to fetch RSS feed for {category}: {exc}")
                continue

            if hasattr(feed.feed, "title") and "Feed error for query" in feed.feed.title:
                report.failed_pages.append(feed_url)
                logger.warning(f"Invalid ARXIV_QUERY: {category}. Skipping.")
                continue

            cat_paper_ids = [
                extract_arxiv_id(entry.id)
                for entry in feed.entries
                if entry.get("arxiv_announce_type", "new") in allowed_announce_types
            ]
            logger.info(f"Found {len(feed.entries)} entries for {category}, {len(cat_paper_ids)} matched types {allowed_announce_types}")
            all_paper_ids.extend(cat_paper_ids)

        unique_ids, duplicate_ids = stable_unique(all_paper_ids)
        report.feed_ids = unique_ids
        report.duplicate_ids = duplicate_ids
        report.expected_count = len(unique_ids)

        raw_papers = self._fetch_metadata_by_ids(unique_ids, report)
        self._finalize_report(report)
        self.last_fetch_report = report
        return raw_papers

    def _retrieve_by_target_date(self, target_date: str) -> list[RawArxivResult]:
        from_stamp, to_stamp = build_announcement_window(target_date)
        report = ArxivFetchReport(mode="submittedDate")
        page_size = int(self.config.executor.get("arxiv_page_size", 800))
        query = self._build_submitted_date_query(from_stamp, to_stamp)
        raw_papers = self._fetch_arxiv_query_pages(query, page_size, report)

        if get_config_bool(self.config.executor, "cross_validate_dailyarxiv", False):
            self._cross_validate_with_dailyarxiv(target_date, raw_papers, report)

        self._finalize_report(report)
        self.last_fetch_report = report
        return raw_papers

    def _build_submitted_date_query(self, from_stamp: str, to_stamp: str) -> str:
        category_query = " OR ".join(category_to_api_term(category) for category in self.categories)
        if len(self.categories) > 1:
            category_query = f"({category_query})"
        return f'{category_query} AND submittedDate:"{from_stamp} TO {to_stamp}"'

    def _fetch_arxiv_query_pages(self, query: str, page_size: int, report: ArxivFetchReport) -> list[RawArxivResult]:
        results: list[RawArxivResult] = []
        expected_count: int | None = None
        start = 0

        while expected_count is None or start < expected_count:
            try:
                page_entries, page_total = self._fetch_arxiv_query_page(query, start, page_size)
            except Exception as exc:
                report.failed_pages.append(f"start={start}: {exc}")
                break

            if expected_count is None:
                expected_count = page_total
                report.expected_count = page_total
                if expected_count == 0:
                    break

            if not page_entries:
                report.failed_pages.append(f"start={start}: empty page before expected count {expected_count}")
                break

            results.extend(page_entries)
            start += len(page_entries)

        fetched_ids = [extract_arxiv_id(result.entry_id) for result in results]
        unique_ids, duplicate_ids = stable_unique(fetched_ids)
        report.duplicate_ids = duplicate_ids
        report.fetched_ids = unique_ids
        report.fetched_count = len(unique_ids)
        if report.expected_count != report.fetched_count:
            report.missing_ids = [f"unknown:{report.expected_count - report.fetched_count}"]
        return results

    def _fetch_arxiv_query_page(self, query: str, start: int, max_results: int) -> tuple[list[RawArxivResult], int]:
        response = requests.get(
            ARXIV_API_URL,
            params={
                "search_query": query,
                "start": start,
                "max_results": max_results,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            timeout=FETCH_TIMEOUT,
        )
        response.raise_for_status()
        return _parse_atom_feed(response.text)

    def _fetch_metadata_by_ids(self, ids: list[str], report: ArxivFetchReport) -> list[ArxivResult]:
        if not ids:
            report.fetched_ids = []
            report.fetched_count = 0
            return []

        client = arxiv.Client(num_retries=3, delay_seconds=3)
        raw_papers: list[ArxivResult] = []
        returned_ids: list[str] = []
        batch_size = int(self.config.executor.get("arxiv_metadata_batch_size", 20))

        bar = tqdm(total=len(ids), desc="Fetching arXiv metadata")
        for index in range(0, len(ids), batch_size):
            batch_ids = ids[index:index + batch_size]
            batch_results: list[ArxivResult] = []
            attempts = 0
            while attempts < 3:
                try:
                    search = arxiv.Search(id_list=batch_ids)
                    batch_results = list(client.results(search))
                    break
                except Exception as exc:
                    attempts += 1
                    logger.warning(f"Failed to fetch arXiv metadata batch {index // batch_size} (attempt {attempts}/3): {exc}")
                    if attempts < 3:
                        time.sleep(min(2 ** attempts, 10))
                    else:
                        report.failed_batches.append(",".join(batch_ids))

            raw_papers.extend(batch_results)
            batch_returned_ids = [extract_arxiv_id(result.entry_id) for result in batch_results]
            returned_ids.extend(batch_returned_ids)
            batch_missing = [paper_id for paper_id in batch_ids if paper_id not in set(batch_returned_ids)]
            report.missing_ids.extend(batch_missing)
            bar.update(len(batch_ids))
        bar.close()

        unique_returned_ids, duplicate_ids = stable_unique(returned_ids)
        report.duplicate_ids.extend(duplicate_id for duplicate_id in duplicate_ids if duplicate_id not in report.duplicate_ids)
        report.fetched_ids = unique_returned_ids
        report.fetched_count = len(unique_returned_ids)
        report.extra_ids = [paper_id for paper_id in unique_returned_ids if paper_id not in set(ids)]
        return raw_papers

    def _cross_validate_with_dailyarxiv(
        self,
        target_date: str,
        raw_papers: list[RawArxivResult],
        report: ArxivFetchReport,
    ) -> None:
        try:
            dailyarxiv_ids = self._fetch_dailyarxiv_ids(target_date)
        except Exception as exc:
            message = f"dailyarxiv cross-validation failed: {exc}"
            report.warnings.append(message)
            logger.warning(message)
            if str(self.config.executor.get("cross_validation_mode", "warn")).lower() == "fail":
                raise ArxivFetchIntegrityError(message) from exc
            return

        local_ids = {extract_arxiv_id(paper.entry_id) for paper in raw_papers}
        daily_ids = set(dailyarxiv_ids)
        report.dailyarxiv_count = len(daily_ids)
        report.cross_validation_missing_ids = sorted(daily_ids - local_ids)
        report.cross_validation_extra_ids = sorted(local_ids - daily_ids)

        if report.has_cross_validation_failures():
            message = (
                "dailyarxiv cross-validation mismatch: "
                f"missing={report.cross_validation_missing_ids}, extra={report.cross_validation_extra_ids}"
            )
            report.warnings.append(message)
            logger.warning(message)

    def _fetch_dailyarxiv_ids(self, target_date: str) -> list[str]:
        current = parse_target_date(target_date)
        previous = current - timedelta(days=1)
        categories = "+OR+".join(category_to_dailyarxiv_term(category) for category in self.categories)
        response = requests.get(
            DAILY_ARXIV_URL,
            params={
                "categories": categories,
                "from": f"{previous:%Y%m%d}",
                "to": f"{current:%Y%m%d}",
            },
            timeout=FETCH_TIMEOUT,
        )
        response.raise_for_status()
        entries, _total = _parse_atom_feed(response.text)
        ids = [extract_arxiv_id(entry.entry_id) for entry in entries]
        unique_ids, _duplicates = stable_unique(ids)
        return unique_ids

    def _finalize_report(self, report: ArxivFetchReport) -> None:
        logger.info(report.summary())
        strict = get_config_bool(self.config.executor, "fetch_strict", True)
        cross_validation_mode = str(self.config.executor.get("cross_validation_mode", "warn")).lower()
        if strict and report.has_primary_failures():
            raise ArxivFetchIntegrityError(report.summary())
        if cross_validation_mode == "fail" and report.has_cross_validation_failures():
            raise ArxivFetchIntegrityError(report.summary())

    def populate_full_text(self, paper: Paper) -> Paper:
        if paper.full_text:
            return paper

        html_ref = SimpleNamespace(entry_id=paper.url, title=paper.title)
        pdf_ref = SimpleNamespace(pdf_url=paper.pdf_url, title=paper.title)
        tar_ref = SimpleNamespace(entry_id=paper.url, title=paper.title, source_url=lambda: f"https://arxiv.org/e-print/{extract_arxiv_id(paper.url)}")

        full_text = extract_text_from_html(html_ref)
        if full_text is None:
            full_text = extract_text_from_pdf(pdf_ref)
        if full_text is None:
            full_text = extract_text_from_tar(tar_ref)
        paper.full_text = full_text
        return paper

    def convert_to_paper(self, raw_paper: ArxivResult | RawArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        paper = Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=raw_paper.entry_id,
            pdf_url=pdf_url,
            full_text=None,
            published_at=getattr(raw_paper, "published", None),
        )
        if self.fetch_full_text_during_retrieval:
            self.populate_full_text(paper)
        return paper


def extract_text_from_html(paper: ArxivResult | RawArxivResult) -> str | None:
    html_url = paper.entry_id.replace("/abs/", "/html/")
    try:
        return _extract_text_from_html_worker(html_url)
    except Exception as exc:
        logger.warning(f"HTML extraction failed for {paper.title}: {exc}")
        return None


def extract_text_from_pdf(paper: ArxivResult | RawArxivResult) -> str | None:
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


def extract_text_from_tar(paper: ArxivResult | RawArxivResult) -> str | None:
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
