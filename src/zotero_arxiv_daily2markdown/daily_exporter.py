from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
from pathlib import Path
import re
from typing import Any

from omegaconf import DictConfig

from .protocol import DomainDecision, Paper

BEIJING_TZ = timezone(timedelta(hours=8))
TIMEZONE_NAME = "Asia/Shanghai"

_ARXIV_ID_RE = re.compile(
    r"(?P<base>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7})|\d{4}\.\d{4,5})(?P<version>v\d+)?",
    flags=re.IGNORECASE,
)


def beijing_now_iso() -> str:
    return datetime.now(BEIJING_TZ).isoformat()


def export_daily_json(
    *,
    accepted_papers: list[Paper],
    display_papers: list[Paper],
    candidate_papers: list[Paper],
    domain_decisions: list[DomainDecision],
    overview_zh: str,
    overview_en: str,
    config: DictConfig,
    announcement_date: str,
    processed_at: str | None,
    report: dict[str, Any],
) -> Path:
    output_dir = resolve_daily_output_dir(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{announcement_date}.json"

    payload = build_daily_payload(
        accepted_papers=accepted_papers,
        display_papers=display_papers,
        candidate_papers=candidate_papers,
        domain_decisions=domain_decisions,
        overview_zh=overview_zh,
        overview_en=overview_en,
        config=config,
        announcement_date=announcement_date,
        processed_at=processed_at,
        report=report,
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_daily_payload(
    *,
    accepted_papers: list[Paper],
    display_papers: list[Paper],
    candidate_papers: list[Paper],
    domain_decisions: list[DomainDecision],
    overview_zh: str,
    overview_en: str,
    config: DictConfig,
    announcement_date: str,
    processed_at: str | None,
    report: dict[str, Any],
) -> dict[str, Any]:
    empty = len(accepted_papers) == 0
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "announcement_date": announcement_date,
        "processed_at": processed_at,
        "generated_at": beijing_now_iso(),
        "timezone": TIMEZONE_NAME,
        "arxiv_window": _publication_window(candidate_papers),
        "query_scope": _query_scope(config),
        "empty": empty,
        "candidate_count": int(report.get("retrieved_count", len(candidate_papers))),
        "longlisted_count": int(report.get("longlisted_count", len(candidate_papers))),
        "accepted_count": int(report.get("accepted_count", len(accepted_papers))),
        "rejected_count": int(
            report.get("rejected_count", sum(1 for decision in domain_decisions if decision.decision == "reject"))
        ),
        "uncertain_count": int(
            report.get("uncertain_count", sum(1 for decision in domain_decisions if decision.decision == "uncertain"))
        ),
        "displayed_count": int(report.get("displayed_count", len(display_papers))),
        "overview": {
            "zh": overview_zh or None,
            "en": overview_en or None,
        },
        "papers": [_paper_payload(paper, index) for index, paper in enumerate(accepted_papers, start=1)],
    }
    if empty:
        payload["empty_reason"] = f"No accepted papers matched the {_query_scope(config)} scope."
    return payload


def resolve_daily_output_dir(config: DictConfig) -> Path:
    hugo_config = config.get("hugo") if hasattr(config, "get") else None
    if hugo_config and hugo_config.get("output_dir"):
        output_dir = Path(str(hugo_config.output_dir)).expanduser()
        repo_dir = output_dir.parent if output_dir.name == "content" else output_dir
        return repo_dir / "data" / "daily"
    return Path("data/daily")


def normalize_arxiv_identity(*values: str | None) -> tuple[str | None, str | None, str | None]:
    for value in values:
        normalized = _normalize_arxiv_value(value)
        match = _ARXIV_ID_RE.search(normalized)
        if not match:
            continue
        base_id = match.group("base")
        version = match.group("version")
        versioned_id = f"{base_id}{version}" if version else base_id
        return base_id, version, versioned_id
    return None, None, None


def title_plain(title: str | None) -> str:
    text = html.unescape(title or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\bxmlns(?::\w+)?\s*=\s*(['\"]).*?\1", " ", text)
    return _collapse_whitespace(text)


def _paper_payload(paper: Paper, rank: int) -> dict[str, Any]:
    base_id, version, versioned_id = normalize_arxiv_identity(paper.arxiv_id, paper.url, paper.pdf_url)
    paper_id = base_id or paper.doi or f"title:{_normalize_title(paper.title)}"
    source_arxiv_id = versioned_id if base_id else None
    return {
        "rank": rank,
        "paper_id": paper_id,
        "arxiv_id": base_id,
        "arxiv_version": version,
        "source_ids": {
            "arxiv": source_arxiv_id,
            "doi": paper.doi,
            "zotero_key": None,
        },
        "title_plain": title_plain(paper.title),
        "title_tex": paper.title,
        "title_original": paper.title,
        "authors": paper.authors or [],
        "affiliations": paper.affiliations or [],
        "categories": paper.categories or [],
        "primary_category": paper.primary_category,
        "abs_url": _arxiv_abs_url(versioned_id),
        "pdf_url": _arxiv_pdf_url(versioned_id),
        "score": _json_float(paper.score),
        "domain_decision": _domain_decision_payload(paper.domain_decision),
        "summary": {
            "zh": paper.tldr,
            "en": paper.tldr_en,
        },
        "summary_status": {
            "zh": _summary_status(paper.tldr),
            "en": _summary_status(paper.tldr_en),
        },
        "source_metadata": {
            "id_type": "arxiv" if base_id else "doi" if paper.doi else "title",
            "full_text_path": paper.full_text_path,
            "pdf_path": paper.pdf_path,
            "text_sha256": paper.text_sha256,
            "pdf_sha256": paper.pdf_sha256,
            "full_text_source": paper.full_text_source,
        },
    }


def _domain_decision_payload(decision: DomainDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "decision": decision.decision,
        "confidence": _json_float(decision.confidence),
        "reason": decision.reason,
        "matched_concepts": decision.matched_concepts,
        "negative_evidence": decision.negative_evidence,
    }


def _publication_window(papers: list[Paper]) -> dict[str, str] | None:
    timestamps = sorted(
        timestamp
        for timestamp in (_to_utc(paper.published_at) for paper in papers)
        if timestamp is not None
    )
    if not timestamps:
        return None
    return {
        "start": timestamps[0].isoformat().replace("+00:00", "Z"),
        "end": timestamps[-1].isoformat().replace("+00:00", "Z"),
    }


def _to_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _query_scope(config: DictConfig) -> str:
    domain_cfg = config.get("domain") or {}
    prompt_cfg = config.get("prompt") or {}
    return str(domain_cfg.get("topic") or prompt_cfg.get("topic") or "research")


def _normalize_arxiv_value(value: str | None) -> str:
    normalized = (value or "").strip()
    for prefix in (
        "oai:arXiv.org:",
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
        "arxiv:",
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    normalized = normalized.split("?", 1)[0].split("#", 1)[0].strip().rstrip("/")
    if normalized.endswith(".pdf"):
        normalized = normalized[:-4]
    return normalized


def _arxiv_abs_url(versioned_id: str | None) -> str | None:
    return f"https://arxiv.org/abs/{versioned_id}" if versioned_id else None


def _arxiv_pdf_url(versioned_id: str | None) -> str | None:
    return f"https://arxiv.org/pdf/{versioned_id}" if versioned_id else None


def _summary_status(value: str | None) -> str:
    return "machine_generated" if value else "missing"


def _json_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
