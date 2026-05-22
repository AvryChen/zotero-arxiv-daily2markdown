from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, TypeVar
from queue import Empty
from xml.etree import ElementTree as ET
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import threading
import time

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
DEFAULT_ARXIV_USER_AGENT = "arXiv Daily: Nickelate Superconductors (support@jxchen.org)"
DEFAULT_ARXIV_PROXY_URL: str | None = None
DOWNLOAD_TIMEOUT = (10, 60)
FETCH_TIMEOUT = 30
PDF_EXTRACT_TIMEOUT = 180
TAR_EXTRACT_TIMEOUT = 180

_ARXIV_REQUEST_LOCK = threading.Lock()
_last_arxiv_request_at: float | None = None

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
        parts = [
            "source=arxiv "
            f"mode={self.mode} "
            f"expected={self.expected_count} "
            f"fetched={self.fetched_count} "
            f"dailyarxiv={dailyarxiv_count} "
            f"missing={len(self.missing_ids) + len(self.cross_validation_missing_ids)} "
            f"extra={len(self.extra_ids) + len(self.cross_validation_extra_ids)} "
            f"duplicates={len(self.duplicate_ids)}"
        ]
        if self.failed_pages:
            parts.append(f"failed_pages={self.failed_pages}")
        if self.failed_batches:
            parts.append(f"failed_batches={self.failed_batches}")
        if self.warnings:
            parts.append(f"warnings={self.warnings}")
        return " ".join(parts)


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
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
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


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _default_arxiv_proxies() -> dict[str, str] | None:
    if not DEFAULT_ARXIV_PROXY_URL:
        return None
    return {"http": DEFAULT_ARXIV_PROXY_URL, "https": DEFAULT_ARXIV_PROXY_URL}


def _retry_after_seconds(response: Any, fallback_seconds: float) -> float:
    retry_after = getattr(response, "headers", {}).get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return fallback_seconds


def _reset_arxiv_request_throttle() -> None:
    global _last_arxiv_request_at
    with _ARXIV_REQUEST_LOCK:
        _last_arxiv_request_at = None


def _reset_arxiv_api_request_throttle() -> None:
    _reset_arxiv_request_throttle()


def _reset_arxiv_rss_request_throttle() -> None:
    _reset_arxiv_request_throttle()


def _sleep_before_arxiv_request(min_interval_seconds: float) -> float | None:
    global _last_arxiv_request_at
    min_interval_seconds = max(0.0, min_interval_seconds)
    if min_interval_seconds <= 0:
        _last_arxiv_request_at = time.monotonic()
        return _last_arxiv_request_at

    now = time.monotonic()
    if _last_arxiv_request_at is not None:
        elapsed = now - _last_arxiv_request_at
        wait_seconds = min_interval_seconds - elapsed
        if wait_seconds > 0:
            logger.debug(f"Waiting {wait_seconds:.1f}s before next arXiv request")
            time.sleep(wait_seconds)
            now = time.monotonic()
    _last_arxiv_request_at = now
    return now


def _sleep_before_arxiv_api_request(min_interval_seconds: float) -> None:
    with _ARXIV_REQUEST_LOCK:
        _sleep_before_arxiv_request(min_interval_seconds)


def _sleep_before_arxiv_rss_request(min_interval_seconds: float) -> None:
    with _ARXIV_REQUEST_LOCK:
        _sleep_before_arxiv_request(min_interval_seconds)


def _perform_arxiv_request(min_interval_seconds: float, request_func: Callable[[], T]) -> T:
    with _ARXIV_REQUEST_LOCK:
        _sleep_before_arxiv_request(min_interval_seconds)
        return request_func()

def _status_code_from_error(exc: Exception, response: Any | None = None) -> int | None:
    error_response = getattr(exc, "response", None)
    return getattr(error_response, "status_code", None) or getattr(response, "status_code", None)


def _jsonable_params(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {key: params[key] for key in sorted(params)}


def _safe_cache_name(kind: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{kind}-{digest}"


def _cache_path(cache_dir: str | os.PathLike[str], kind: str, payload: dict[str, Any], suffix: str) -> Path:
    return Path(cache_dir) / f"{_safe_cache_name(kind, payload)}{suffix}"


def _read_cache_bytes(cache_dir: str | os.PathLike[str], kind: str, payload: dict[str, Any]) -> bytes | None:
    path = _cache_path(cache_dir, kind, payload, ".bin")
    try:
        if path.exists():
            logger.debug(f"Using arXiv cache {path}")
            return path.read_bytes()
    except OSError as exc:
        logger.warning(f"Failed to read arXiv cache {path}: {exc}")
    return None


def _write_cache_bytes(cache_dir: str | os.PathLike[str], kind: str, payload: dict[str, Any], content: bytes) -> None:
    path = _cache_path(cache_dir, kind, payload, ".bin")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    except OSError as exc:
        logger.warning(f"Failed to write arXiv cache {path}: {exc}")


def _read_cache_json(cache_dir: str | os.PathLike[str], kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    path = _cache_path(cache_dir, kind, payload, ".json")
    try:
        if path.exists():
            logger.debug(f"Using arXiv cache {path}")
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to read arXiv cache {path}: {exc}")
    return None


def _write_cache_json(cache_dir: str | os.PathLike[str], kind: str, payload: dict[str, Any], content: dict[str, Any]) -> None:
    path = _cache_path(cache_dir, kind, payload, ".json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Failed to write arXiv cache {path}: {exc}")


def _write_cache_failure(
    cache_dir: str | os.PathLike[str],
    kind: str,
    payload: dict[str, Any],
    exc: Exception,
    *,
    status_code: int | None = None,
) -> None:
    path = _cache_path(cache_dir, kind, payload, ".failed.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "status_code": status_code,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as write_exc:
        logger.warning(f"Failed to write arXiv failure cache {path}: {write_exc}")


def parse_arxiv_datetime(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(f"Could not parse arXiv timestamp: {value}")
        return None


def _download_file(url: str, path: str) -> None:
    with _ARXIV_REQUEST_LOCK:
        _sleep_before_arxiv_request(5)
        response = requests.get(
            url,
            headers={"User-Agent": DEFAULT_ARXIV_USER_AGENT},
            proxies=_default_arxiv_proxies(),
            stream=True,
            timeout=DOWNLOAD_TIMEOUT,
        )
        with response:
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

    response = _perform_arxiv_request(
        5,
        lambda: requests.get(
            html_url,
            headers={"User-Agent": DEFAULT_ARXIV_USER_AGENT},
            proxies=_default_arxiv_proxies(),
            timeout=FETCH_TIMEOUT,
        ),
    )
    response.raise_for_status()
    text = trafilatura.extract(response.text, include_comments=False, include_tables=False)
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


def _clean_rss_summary(summary: Any) -> str:
    summary_text = re.sub(r"\s+", " ", str(summary or "")).strip()
    summary_text = re.sub(
        r"^arXiv:\S+\s+Announce Type:\s+\S+\s*",
        "",
        summary_text,
        flags=re.IGNORECASE,
    ).strip()
    summary_text = re.sub(r"^Abstract:\s*", "", summary_text, flags=re.IGNORECASE).strip()
    return summary_text


def _rss_entry_authors(entry: Any) -> list[RawArxivAuthor]:
    names: list[str] = []
    for author in entry.get("authors", []) or []:
        name = author.get("name") if isinstance(author, dict) else getattr(author, "name", None)
        if name:
            names.extend(part.strip() for part in str(name).split(","))
    if not names and entry.get("author"):
        names.extend(part.strip() for part in str(entry.get("author")).split(","))
    return [RawArxivAuthor(name=re.sub(r"\s+", " ", name).strip()) for name in names if name.strip()]


def _raw_result_from_rss_entry(entry: Any) -> RawArxivResult:
    paper_id = extract_arxiv_id(str(entry.id))
    categories = [
        tag.get("term")
        for tag in (entry.get("tags", []) or [])
        if isinstance(tag, dict) and tag.get("term")
    ]
    entry_id = f"https://arxiv.org/abs/{paper_id}"
    return RawArxivResult(
        title=re.sub(r"\s+", " ", str(entry.get("title", ""))).strip(),
        authors=_rss_entry_authors(entry),
        summary=_clean_rss_summary(entry.get("summary", "")),
        pdf_url=f"https://arxiv.org/pdf/{paper_id}",
        entry_id=entry_id,
        published=entry.get("published"),
        updated=entry.get("updated"),
        categories=categories,
        primary_category=categories[0] if categories else None,
    )


@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
        self.last_fetch_report: ArxivFetchReport | None = None
        self.fetch_full_text_during_retrieval = False

    @property
    def categories(self) -> list[str]:
        categories = self.config.source.arxiv.category
        if isinstance(categories, str):
            return [categories]
        return list(categories)

    @property
    def arxiv_user_agent(self) -> str:
        return str(self.config.executor.get("arxiv_user_agent", DEFAULT_ARXIV_USER_AGENT))

    @property
    def arxiv_cache_enabled(self) -> bool:
        return get_config_bool(self.config.executor, "arxiv_cache_enabled", True)

    @property
    def arxiv_cache_dir(self) -> str:
        return str(self.config.executor.get("arxiv_cache_dir", "outputs/cache/arxiv"))

    def _arxiv_headers(self) -> dict[str, str]:
        return {"User-Agent": self.arxiv_user_agent}

    def _arxiv_proxies(self) -> dict[str, str] | None:
        enabled_value = self.config.executor.get("arxiv_proxy_enabled", None)
        if enabled_value is None:
            enabled = to_bool(os.getenv("ARXIV_PROXY_ENABLED", False))
        else:
            enabled = to_bool(enabled_value)
        if not enabled:
            return None

        proxy_url = _blank_to_none(self.config.executor.get("arxiv_proxy_url", None))
        if proxy_url is None:
            proxy_url = _blank_to_none(os.getenv("ARXIV_PROXY_URL"))
        if proxy_url is None:
            return None

        proxies = {"http": proxy_url, "https": proxy_url}
        no_proxy = _blank_to_none(self.config.executor.get("arxiv_proxy_no_proxy", None))
        if no_proxy is None:
            no_proxy = _blank_to_none(os.getenv("ARXIV_PROXY_NO_PROXY"))
        if no_proxy:
            proxies["no_proxy"] = no_proxy
        return proxies

    def _arxiv_request_interval(self) -> float:
        return max(0.0, float(self.config.executor.get("arxiv_request_interval_seconds", 5)))

    def _cache_payload(
        self,
        *,
        url: str,
        params: dict[str, Any] | None = None,
        scope: str | None = None,
        paper_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "url": url,
            "params": _jsonable_params(params),
            "scope": scope,
            "paper_id": paper_id,
        }

    def _arxiv_get_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: Any = FETCH_TIMEOUT,
        cache_kind: str,
        cache_scope: str | None = None,
        paper_id: str | None = None,
        cache_success: bool = True,
    ) -> bytes:
        payload = self._cache_payload(url=url, params=params, scope=cache_scope, paper_id=paper_id)
        if cache_success and self.arxiv_cache_enabled:
            cached = _read_cache_bytes(self.arxiv_cache_dir, cache_kind, payload)
            if cached is not None:
                return cached

        if cache_kind == "rss":
            retries = max(1, int(self.config.executor.get("arxiv_rss_retries", self.config.executor.get("arxiv_query_retries", 5))))
            base_delay = float(self.config.executor.get("arxiv_rss_retry_base_seconds", self.config.executor.get("arxiv_retry_base_seconds", 10)))
            max_delay = float(self.config.executor.get("arxiv_rss_retry_max_seconds", self.config.executor.get("arxiv_retry_max_seconds", 120)))
            cooldown_retries = max(0, int(self.config.executor.get("arxiv_rss_cooldown_retries", 1)))
            cooldown_seconds = float(self.config.executor.get("arxiv_rss_cooldown_seconds", self.config.executor.get("arxiv_429_cooldown_seconds", 300)))
        else:
            retries = max(1, int(self.config.executor.get("arxiv_query_retries", 5)))
            base_delay = float(self.config.executor.get("arxiv_retry_base_seconds", 10))
            max_delay = float(self.config.executor.get("arxiv_retry_max_seconds", 120))
            cooldown_retries = max(0, int(self.config.executor.get("arxiv_429_cooldown_retries", 1)))
            cooldown_seconds = float(
                self.config.executor.get(
                    "arxiv_failure_cooldown_seconds",
                    self.config.executor.get("arxiv_429_cooldown_seconds", 300),
                )
            )

        attempt = 0
        cooldowns_used = 0
        while True:
            attempt += 1
            response = None
            try:
                response = _perform_arxiv_request(
                    self._arxiv_request_interval(),
                    lambda: requests.get(
                        url,
                        params=params,
                        headers=self._arxiv_headers(),
                        proxies=self._arxiv_proxies(),
                        timeout=timeout,
                    ),
                )
                response.raise_for_status()
                content = response.content
                if cache_success and self.arxiv_cache_enabled:
                    _write_cache_bytes(self.arxiv_cache_dir, cache_kind, payload, content)
                return content
            except requests.HTTPError as exc:
                status_code = _status_code_from_error(exc, response)
                should_retry = status_code in {403, 429} or (status_code is not None and 500 <= status_code < 600)
                if not should_retry:
                    if self.arxiv_cache_enabled:
                        _write_cache_failure(self.arxiv_cache_dir, cache_kind, payload, exc, status_code=status_code)
                    raise
                if attempt >= retries:
                    if cooldowns_used < cooldown_retries:
                        cooldowns_used += 1
                        http_cooldown_seconds = (
                            float(self.config.executor.get("arxiv_429_cooldown_seconds", cooldown_seconds))
                            if status_code == 429 and cache_kind != "rss"
                            else cooldown_seconds
                        )
                        wait_seconds = _retry_after_seconds(response, http_cooldown_seconds)
                        logger.warning(
                            f"arXiv request failed with HTTP {status_code} for {url}. "
                            f"Cooling down for {wait_seconds:.1f}s "
                            f"({cooldowns_used}/{cooldown_retries})"
                        )
                        time.sleep(wait_seconds)
                        attempt = 0
                        continue
                    if self.arxiv_cache_enabled:
                        _write_cache_failure(self.arxiv_cache_dir, cache_kind, payload, exc, status_code=status_code)
                    raise
                fallback_delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                wait_seconds = _retry_after_seconds(response, fallback_delay)
                logger.warning(
                    f"arXiv request returned HTTP {status_code} for {url}. "
                    f"Retrying in {wait_seconds:.1f}s ({attempt}/{retries})"
                )
                time.sleep(wait_seconds)
            except requests.RequestException as exc:
                if attempt >= retries:
                    if cooldowns_used < cooldown_retries:
                        cooldowns_used += 1
                        logger.warning(
                            f"arXiv request failed for {url} after {attempt} attempts: {exc}. "
                            f"Cooling down for {cooldown_seconds:.1f}s "
                            f"({cooldowns_used}/{cooldown_retries})"
                        )
                        time.sleep(cooldown_seconds)
                        attempt = 0
                        continue
                    if self.arxiv_cache_enabled:
                        _write_cache_failure(self.arxiv_cache_dir, cache_kind, payload, exc)
                    raise
                wait_seconds = min(max_delay, base_delay * (2 ** (attempt - 1)))
                logger.warning(
                    f"arXiv request failed for {url}: {exc}. "
                    f"Retrying in {wait_seconds:.1f}s ({attempt}/{retries})"
                )
                time.sleep(wait_seconds)

    def _arxiv_get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: Any = FETCH_TIMEOUT,
        cache_kind: str,
        cache_scope: str | None = None,
        paper_id: str | None = None,
        cache_success: bool = True,
    ) -> str:
        content = self._arxiv_get_bytes(
            url,
            params=params,
            timeout=timeout,
            cache_kind=cache_kind,
            cache_scope=cache_scope,
            paper_id=paper_id,
            cache_success=cache_success,
        )
        return content.decode("utf-8", errors="replace")

    def _read_full_text_cache(self, paper_id: str) -> str | None:
        if not self.arxiv_cache_enabled:
            return None
        cached = _read_cache_json(self.arxiv_cache_dir, "fulltext", {"paper_id": paper_id})
        if cached and isinstance(cached.get("text"), str):
            return cached["text"]
        return None

    def _write_full_text_cache(self, paper_id: str, text: str) -> None:
        if self.arxiv_cache_enabled:
            _write_cache_json(self.arxiv_cache_dir, "fulltext", {"paper_id": paper_id}, {"paper_id": paper_id, "text": text})

    def _write_full_text_failure(self, kind: str, paper_id: str, exc: Exception) -> None:
        if self.arxiv_cache_enabled:
            _write_cache_failure(self.arxiv_cache_dir, kind, {"paper_id": paper_id}, exc)

    def _retrieve_raw_papers(self) -> list[ArxivResult | RawArxivResult]:
        target_date = self.config.executor.get("target_date")
        if target_date:
            raw_papers = self._retrieve_by_target_date(str(target_date))
        else:
            raw_papers = self._retrieve_latest_from_rss()

        if self.config.executor.debug:
            raw_papers = raw_papers[:10]
        return raw_papers

    def _retrieve_latest_from_rss(self) -> list[RawArxivResult]:
        report = ArxivFetchReport(mode="rss")
        include_cross_list = get_config_bool(self.config.source.arxiv, "include_cross_list", False)
        allowed_announce_types = {"new", "cross"} if include_cross_list else {"new"}

        all_paper_ids: list[str] = []
        raw_papers_by_id: dict[str, RawArxivResult] = {}
        for category in self.categories:
            feed_url = f"https://rss.arxiv.org/atom/{category}"
            logger.debug(f"Fetching arxiv rss feed from {feed_url}")
            try:
                feed = self._fetch_arxiv_rss_feed(category)
            except Exception as exc:
                report.failed_pages.append(feed_url)
                logger.warning(f"Failed to fetch RSS feed for {category}: {exc}")
                continue

            if hasattr(feed.feed, "title") and "Feed error for query" in feed.feed.title:
                report.failed_pages.append(feed_url)
                logger.warning(f"Invalid ARXIV_QUERY: {category}. Skipping.")
                continue

            cat_entries = [
                entry
                for entry in feed.entries
                if entry.get("arxiv_announce_type", "new") in allowed_announce_types
            ]
            cat_paper_ids = [extract_arxiv_id(str(entry.id)) for entry in cat_entries]
            logger.info(f"Found {len(feed.entries)} entries for {category}, {len(cat_paper_ids)} matched types {allowed_announce_types}")
            all_paper_ids.extend(cat_paper_ids)
            for entry in cat_entries:
                paper_id = extract_arxiv_id(str(entry.id))
                if paper_id not in raw_papers_by_id:
                    raw_papers_by_id[paper_id] = _raw_result_from_rss_entry(entry)

        unique_ids, duplicate_ids = stable_unique(all_paper_ids)
        report.feed_ids = unique_ids
        report.duplicate_ids = duplicate_ids
        report.expected_count = len(unique_ids)
        raw_papers = [raw_papers_by_id[paper_id] for paper_id in unique_ids if paper_id in raw_papers_by_id]
        report.fetched_ids = [extract_arxiv_id(result.entry_id) for result in raw_papers]
        report.fetched_count = len(report.fetched_ids)
        report.missing_ids = [paper_id for paper_id in unique_ids if paper_id not in set(report.fetched_ids)]
        self.last_fetch_report = report
        self._finalize_report(report)
        return raw_papers

    def _fetch_arxiv_rss_feed(self, category: str) -> Any:
        feed_url = f"https://rss.arxiv.org/atom/{category}"
        content = self._arxiv_get_bytes(
            feed_url,
            cache_kind="rss",
            cache_scope=datetime.now().date().isoformat(),
        )
        return feedparser.parse(content)

    def _retrieve_by_target_date(self, target_date: str) -> list[RawArxivResult]:
        from_stamp, to_stamp = build_announcement_window(target_date)
        report = ArxivFetchReport(mode="submittedDate")
        page_size = int(self.config.executor.get("arxiv_page_size", 800))
        query = self._build_submitted_date_query(from_stamp, to_stamp)
        raw_papers = self._fetch_arxiv_query_pages(query, page_size, report)

        if get_config_bool(self.config.executor, "cross_validate_dailyarxiv", False):
            self._cross_validate_with_dailyarxiv(target_date, raw_papers, report)

        self.last_fetch_report = report
        self._finalize_report(report)
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
        params = {
            "search_query": query,
            "start": start,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        text = self._arxiv_get_text(
            ARXIV_API_URL,
            params=params,
            cache_kind="api_query",
        )
        return _parse_atom_feed(text)

    def _fetch_metadata_by_ids(self, ids: list[str], report: ArxivFetchReport) -> list[RawArxivResult]:
        if not ids:
            report.fetched_ids = []
            report.fetched_count = 0
            return []

        raw_papers: list[RawArxivResult] = []
        returned_ids: list[str] = []
        batch_size = int(self.config.executor.get("arxiv_metadata_batch_size", 20))

        bar = tqdm(total=len(ids), desc="Fetching arXiv metadata")
        for index in range(0, len(ids), batch_size):
            batch_ids = ids[index:index + batch_size]
            batch_results: list[RawArxivResult] = []
            try:
                text = self._arxiv_get_text(
                    ARXIV_API_URL,
                    params={"id_list": ",".join(batch_ids), "max_results": len(batch_ids)},
                    cache_kind="api_metadata",
                )
                batch_results, _total = _parse_atom_feed(text)
            except Exception as exc:
                logger.warning(f"Failed to fetch arXiv metadata batch {index // batch_size}: {exc}")
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

        paper_id = extract_arxiv_id(paper.url)
        cached_text = self._read_full_text_cache(paper_id)
        if cached_text:
            paper.full_text = cached_text
            return paper

        html_ref = SimpleNamespace(entry_id=paper.url, title=paper.title)
        pdf_ref = SimpleNamespace(pdf_url=paper.pdf_url, title=paper.title)
        tar_ref = SimpleNamespace(entry_id=paper.url, title=paper.title, source_url=lambda: f"https://arxiv.org/e-print/{extract_arxiv_id(paper.url)}")

        full_text = self.extract_text_from_html(html_ref)
        if full_text is None:
            full_text = self.extract_text_from_pdf(pdf_ref)
        if full_text is None:
            full_text = self.extract_text_from_tar(tar_ref)
        if full_text:
            self._write_full_text_cache(paper_id, full_text)
        paper.full_text = full_text
        return paper

    def extract_text_from_html(self, paper: ArxivResult | RawArxivResult) -> str | None:
        paper_id = extract_arxiv_id(paper.entry_id)
        html_url = paper.entry_id.replace("/abs/", "/html/")
        try:
            html = self._arxiv_get_text(
                html_url,
                cache_kind="html_download",
                paper_id=paper_id,
                cache_success=False,
            )
            import trafilatura

            text = trafilatura.extract(html, include_comments=False, include_tables=False)
            if not text:
                raise ValueError(f"No text extracted from {html_url}")
            return text
        except Exception as exc:
            self._write_full_text_failure("fulltext_html", paper_id, exc)
            logger.warning(f"HTML extraction failed for {paper.title}: {exc}")
            return None

    def extract_text_from_pdf(self, paper: ArxivResult | RawArxivResult) -> str | None:
        if paper.pdf_url is None:
            logger.warning(f"No PDF URL available for {paper.title}")
            return None
        paper_id = extract_arxiv_id(getattr(paper, "entry_id", paper.pdf_url))
        try:
            content = self._arxiv_get_bytes(
                paper.pdf_url,
                timeout=DOWNLOAD_TIMEOUT,
                cache_kind="pdf_download",
                paper_id=paper_id,
                cache_success=False,
            )
            with TemporaryDirectory() as temp_dir:
                path = os.path.join(temp_dir, "paper.pdf")
                Path(path).write_bytes(content)
                return _run_with_hard_timeout(
                    extract_markdown_from_pdf,
                    (path,),
                    timeout=PDF_EXTRACT_TIMEOUT,
                    operation="PDF extraction",
                    paper_title=paper.title,
                )
        except Exception as exc:
            self._write_full_text_failure("fulltext_pdf", paper_id, exc)
            logger.warning(f"PDF extraction failed for {paper.title}: {exc}")
            return None

    def extract_text_from_tar(self, paper: ArxivResult | RawArxivResult) -> str | None:
        source_url = paper.source_url()
        if source_url is None:
            logger.warning(f"No source URL available for {paper.title}")
            return None
        paper_id = extract_arxiv_id(paper.entry_id)
        try:
            content = self._arxiv_get_bytes(
                source_url,
                timeout=DOWNLOAD_TIMEOUT,
                cache_kind="source_download",
                paper_id=paper_id,
                cache_success=False,
            )
            with TemporaryDirectory() as temp_dir:
                path = os.path.join(temp_dir, "paper.tar.gz")
                Path(path).write_bytes(content)
                file_contents = _run_with_hard_timeout(
                    extract_tex_code_from_tar,
                    (path, paper_id),
                    timeout=TAR_EXTRACT_TIMEOUT,
                    operation="Tar extraction",
                    paper_title=paper.title,
                )
                if not file_contents or "all" not in file_contents or not file_contents["all"]:
                    raise ValueError("Main tex file not found.")
                return file_contents["all"]
        except Exception as exc:
            self._write_full_text_failure("fulltext_source", paper_id, exc)
            logger.warning(f"Tar extraction failed for {paper.title}: {exc}")
            return None

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
            published_at=parse_arxiv_datetime(getattr(raw_paper, "published", None)),
        )
        if self.fetch_full_text_during_retrieval:
            self.populate_full_text(paper)
        return paper


def extract_text_from_html(paper: ArxivResult | RawArxivResult) -> str | None:
    html_url = paper.entry_id.replace("/abs/", "/html/")
    try:
        response = _perform_arxiv_request(
            5,
            lambda: requests.get(
                html_url,
                headers={"User-Agent": DEFAULT_ARXIV_USER_AGENT},
                proxies=_default_arxiv_proxies(),
                timeout=FETCH_TIMEOUT,
            ),
        )
        response.raise_for_status()
        import trafilatura

        text = trafilatura.extract(response.text, include_comments=False, include_tables=False)
        if not text:
            raise ValueError(f"No text extracted from {html_url}")
        return text
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
