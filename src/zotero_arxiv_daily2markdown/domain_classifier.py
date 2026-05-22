from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger
from omegaconf import DictConfig

from .protocol import DomainDecision, Paper


def classify_domain_papers(
    papers: list[Paper],
    config: DictConfig,
    openai_client: Any,
) -> list[DomainDecision]:
    domain_config = config.get("domain", {})
    topic = str(domain_config.get("topic") or config.get("prompt", {}).get("topic") or "").strip()
    use_ai = bool(domain_config.get("use_ai", True))
    threshold = float(domain_config.get("ai_confidence_threshold", 0.5))

    if not papers:
        return []
    if not topic:
        return [_accepted_without_ai(paper, "No domain topic configured.") for paper in papers]
    if not use_ai:
        return [
            _accepted_without_ai(paper, "Passed local longlist filtering; AI domain filtering disabled.")
            for paper in papers
        ]

    prompt = _build_prompt(papers, topic, domain_config.get("canonical_vocabulary_path"))
    try:
        response = openai_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You classify whether scientific papers belong to a specific research domain. Return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            **config.llm.get("generation_kwargs", {}),
        )
        content = response.choices[0].message.content or ""
        payload = json.loads(_json_text(content))
    except json.JSONDecodeError as exc:
        logger.warning(f"Domain classifier returned malformed JSON: {exc}")
        return [_uncertain_for_failure(paper, f"malformed_json: {exc}") for paper in papers]
    except Exception as exc:
        logger.warning(f"Domain classification failed: {exc}")
        return [_uncertain_for_failure(paper, f"{type(exc).__name__}: {exc}") for paper in papers]

    by_id = {_paper_id(paper): paper for paper in papers}
    decisions: list[DomainDecision] = []
    items = payload if isinstance(payload, list) else payload.get("papers", [])
    seen: set[str] = set()
    for item in items:
        paper_id = str(item.get("paper_id", "")).strip()
        if not paper_id or paper_id not in by_id:
            continue
        decision_value = str(item.get("decision", "uncertain")).strip().lower() or "uncertain"
        confidence = float(item.get("confidence", 0.0) or 0.0)
        is_in_domain = bool(item.get("is_in_domain", False))
        accepted = is_in_domain and decision_value == "accept" and confidence >= threshold
        decision = DomainDecision(
            paper_id=paper_id,
            is_in_domain=is_in_domain,
            confidence=confidence,
            decision=decision_value,
            reason=str(item.get("reason", "")).strip(),
            matched_concepts=[str(value).strip() for value in item.get("matched_concepts", []) if str(value).strip()],
            negative_evidence=[str(value).strip() for value in item.get("negative_evidence", []) if str(value).strip()],
            accepted=accepted,
        )
        by_id[paper_id].domain_decision = decision
        decisions.append(decision)
        seen.add(paper_id)

    for paper in papers:
        paper_id = _paper_id(paper)
        if paper_id not in seen:
            decision = _uncertain_for_failure(paper, "Domain classifier omitted this paper from the response.")
            paper.domain_decision = decision
            decisions.append(decision)
    return decisions


def _accepted_without_ai(paper: Paper, reason: str) -> DomainDecision:
    decision = DomainDecision(
        paper_id=_paper_id(paper),
        is_in_domain=True,
        confidence=1.0,
        decision="accept",
        reason=reason,
        accepted=True,
    )
    paper.domain_decision = decision
    return decision


def _uncertain_for_failure(paper: Paper, reason: str) -> DomainDecision:
    decision = DomainDecision(
        paper_id=_paper_id(paper),
        is_in_domain=False,
        confidence=0.0,
        decision="uncertain",
        reason=reason,
        accepted=False,
    )
    paper.domain_decision = decision
    return decision


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


def _json_text(content: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", content, flags=re.DOTALL)
    return match.group(1).strip() if match else content.strip()


def _build_prompt(papers: list[Paper], topic: str, vocabulary_path: str | None) -> str:
    vocabulary = _read_vocabulary(vocabulary_path)
    payload = [
        {
            "paper_id": _paper_id(paper),
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "categories": paper.categories,
            "primary_category": paper.primary_category,
            "published_at": paper.published_at.isoformat() if hasattr(paper.published_at, "isoformat") else paper.published_at,
            "embedding_score": paper.score,
            "matched_zotero_examples": [],
        }
        for paper in papers
    ]
    return (
        f"Research domain: {topic}\n\n"
        "Decide whether each paper should be accepted into this domain-specific arXiv capture. "
        "Use only title, abstract, categories, score, and optional canonical vocabulary. "
        "Do not accept papers only because they use broad methods or generic background terms.\n\n"
        "Return JSON with this exact shape:\n"
        '{"papers":[{"paper_id":"...","is_in_domain":true,"confidence":0.0,"decision":"accept|reject|uncertain","reason":"...","matched_concepts":["..."],"negative_evidence":["..."]}]}\n\n'
        f"Canonical vocabulary:\n{vocabulary}\n\n"
        f"Paper metadata:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _read_vocabulary(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")[:12000]
    except OSError as exc:
        logger.warning(f"Failed to read canonical vocabulary {path}: {exc}")
        return ""
