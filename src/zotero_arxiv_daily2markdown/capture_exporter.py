from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any

from omegaconf import DictConfig

from .protocol import DomainDecision, Paper
from .utils import extract_markdown_from_pdf


@dataclass
class CaptureExportResult:
    captured_count: int
    output_dir: str
    papers_path: str
    domain_decisions_path: str
    rejected_candidates_path: str
    run_report_path: str


def export_capture_artifacts(
    *,
    accepted_papers: list[Paper],
    candidate_papers: list[Paper],
    domain_decisions: list[DomainDecision],
    config: DictConfig,
    run_date: str,
    report: dict[str, Any],
) -> CaptureExportResult:
    capture_config = config.get("capture", {})
    output_dir = Path(str(capture_config.get("output_dir", "data/capture"))).expanduser()
    fulltext_dir = Path(str(capture_config.get("fulltext_dir", output_dir / "fulltext"))).expanduser()
    arxiv_fulltext_dir = fulltext_dir / "arxiv"
    output_dir.mkdir(parents=True, exist_ok=True)
    arxiv_fulltext_dir.mkdir(parents=True, exist_ok=True)

    for paper in accepted_papers:
        _write_fulltext_artifacts(paper, arxiv_fulltext_dir, capture_config)

    accepted_records = [_paper_record(paper) for paper in accepted_papers]
    accepted_ids = {_paper_id(paper) for paper in accepted_papers}
    rejected_records = [
        _paper_record(paper)
        for paper in candidate_papers
        if _paper_id(paper) not in accepted_ids
    ]

    papers_path = output_dir / "papers.jsonl"
    decisions_path = output_dir / "domain_decisions.json"
    rejected_path = output_dir / "rejected_candidates.jsonl"
    run_path = output_dir / "runs" / f"{run_date}.json"

    _upsert_jsonl(papers_path, accepted_records)
    _upsert_json_array(decisions_path, [decision.to_dict() for decision in domain_decisions])
    if bool(capture_config.get("save_rejected_candidates", True)):
        _upsert_jsonl(rejected_path, rejected_records)
    else:
        rejected_path.write_text("", encoding="utf-8")

    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_payload = {
        "run_date": run_date,
        "captured_at": datetime.now().astimezone().isoformat(),
        "captured_count": len(accepted_papers),
        "accepted_paper_ids": [_paper_id(paper) for paper in accepted_papers],
        "candidate_count": len(candidate_papers),
        "domain_decision_count": len(domain_decisions),
        "paths": {
            "papers": str(papers_path),
            "domain_decisions": str(decisions_path),
            "rejected_candidates": str(rejected_path),
            "fulltext_dir": str(arxiv_fulltext_dir),
        },
        **report,
    }
    _write_json(run_path, run_payload)

    return CaptureExportResult(
        captured_count=len(accepted_papers),
        output_dir=str(output_dir),
        papers_path=str(papers_path),
        domain_decisions_path=str(decisions_path),
        rejected_candidates_path=str(rejected_path),
        run_report_path=str(run_path),
    )


def _write_fulltext_artifacts(paper: Paper, arxiv_fulltext_dir: Path, capture_config: Any) -> None:
    paper_id = _paper_id(paper)
    text_path = arxiv_fulltext_dir / f"{paper_id}.txt"
    pdf_path = arxiv_fulltext_dir / f"{paper_id}.pdf"
    meta_path = arxiv_fulltext_dir / f"{paper_id}.meta.json"

    full_text_available = bool(paper.full_text)
    used_abstract_fallback = False
    text = paper.full_text or ""
    if not text and paper.pdf_bytes:
        try:
            extracted_text = _extract_text_from_pdf_bytes(paper.pdf_bytes, paper_id)
            if extracted_text:
                text = extracted_text
                paper.full_text = extracted_text
                paper.full_text_source = "pdf"
                full_text_available = True
            else:
                paper.full_text_errors.setdefault("pdf_to_text", "PDF text extraction returned no text.")
        except Exception as exc:
            paper.full_text_errors["pdf_to_text"] = f"{type(exc).__name__}: {exc}"
    if not text:
        text = paper.abstract or ""
        used_abstract_fallback = True

    if bool(capture_config.get("save_full_text", True)):
        text_path.write_text(text, encoding="utf-8")
        paper.full_text_path = str(text_path)
        paper.text_sha256 = _sha256_bytes(text.encode("utf-8"))

    if bool(capture_config.get("save_pdf", True)) and paper.pdf_bytes:
        pdf_path.write_bytes(paper.pdf_bytes)
        paper.pdf_path = str(pdf_path)
        paper.pdf_sha256 = _sha256_bytes(paper.pdf_bytes)

    if bool(capture_config.get("save_meta", True)):
        meta = {
            "paper_id": paper_id,
            "full_text_source": paper.full_text_source,
            "full_text_available": full_text_available,
            "used_abstract_fallback": used_abstract_fallback,
            "text_length": len(text),
            "text_path": paper.full_text_path,
            "pdf_path": paper.pdf_path,
            "text_sha256": paper.text_sha256,
            "pdf_sha256": paper.pdf_sha256,
            "errors": paper.full_text_errors,
        }
        _write_json(meta_path, meta)


def _extract_text_from_pdf_bytes(pdf_bytes: bytes, paper_id: str) -> str | None:
    with TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / f"{paper_id}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        text = extract_markdown_from_pdf(str(pdf_path))
    return text.strip() if text and text.strip() else None


def _paper_record(paper: Paper) -> dict[str, Any]:
    decision = paper.domain_decision.to_dict() if paper.domain_decision else None
    return {
        "paper_id": _paper_id(paper),
        "id_type": "arxiv" if (paper.arxiv_id or _extract_arxiv_id(paper.url)) else "doi" if paper.doi else "title",
        "arxiv_id": paper.arxiv_id or _extract_arxiv_id(paper.url),
        "doi": paper.doi or "",
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "url": paper.url,
        "pdf_url": paper.pdf_url or "",
        "published_at": _json_datetime(paper.published_at),
        "updated_at": _json_datetime(paper.updated_at),
        "categories": paper.categories,
        "primary_category": paper.primary_category,
        "source": paper.source,
        "embedding_score": paper.score,
        "summary_zh": paper.tldr or "",
        "summary_en": paper.tldr_en or "",
        "affiliations": paper.affiliations or [],
        "domain_decision": decision,
        "text": {
            "abstract_available": bool(paper.abstract),
            "full_text_available": bool(paper.full_text),
            "full_text_source": paper.full_text_source,
            "full_text_path": paper.full_text_path,
            "pdf_path": paper.pdf_path,
            "content_sha256": paper.text_sha256,
            "pdf_sha256": paper.pdf_sha256,
        },
        "captured_at": datetime.now().astimezone().isoformat(),
    }


def _paper_id(paper: Paper) -> str:
    return paper.arxiv_id or _extract_arxiv_id(paper.url) or paper.doi or f"title:{_normalize_title(paper.title)}"


def _extract_arxiv_id(value: str | None) -> str:
    value = (value or "").strip()
    for prefix in (
        "oai:arXiv.org:",
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://arxiv.org/pdf/",
        "http://arxiv.org/pdf/",
    ):
        if value.startswith(prefix):
            return value.removeprefix(prefix).rstrip("/")
    return ""


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _json_datetime(value: Any) -> str:
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _upsert_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            existing[str(item.get("paper_id"))] = item
    for row in rows:
        existing[str(row.get("paper_id"))] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + os.linesep for row in existing.values()),
        encoding="utf-8",
    )


def _upsert_json_array(path: Path, rows: list[dict[str, Any]]) -> None:
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for item in json.loads(path.read_text(encoding="utf-8")):
            existing[str(item.get("paper_id"))] = item
    for row in rows:
        existing[str(row.get("paper_id"))] = row
    _write_json(path, list(existing.values()))
