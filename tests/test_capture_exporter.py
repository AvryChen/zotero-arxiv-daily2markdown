import json
from pathlib import Path

from omegaconf import OmegaConf

from zotero_arxiv_daily2markdown import capture_exporter
from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily2markdown.capture_exporter import export_capture_artifacts
from zotero_arxiv_daily2markdown.protocol import DomainDecision


def _config(tmp_path: Path):
    return OmegaConf.create(
        {
            "capture": {
                "enabled": True,
                "output_dir": str(tmp_path / "capture"),
                "fulltext_dir": str(tmp_path / "capture" / "fulltext"),
                "save_pdf": True,
                "save_full_text": True,
                "save_meta": True,
                "save_rejected_candidates": True,
                "id_priority": ["arxiv", "doi", "title"],
            }
        }
    )


def test_export_capture_artifacts_writes_accepted_rejected_and_run_report(tmp_path):
    accepted = make_sample_paper(
        arxiv_id="2605.00001v1",
        title="Accepted Paper",
        url="https://arxiv.org/abs/2605.00001v1",
        pdf_url="https://arxiv.org/pdf/2605.00001v1",
        full_text="Full accepted text",
        full_text_source="html",
        pdf_bytes=b"%PDF accepted",
        score=5.2,
        categories=["cond-mat.supr-con"],
        primary_category="cond-mat.supr-con",
    )
    rejected = make_sample_paper(
        arxiv_id="2605.00002v1",
        title="Rejected Paper",
        url="https://arxiv.org/abs/2605.00002v1",
        score=5.1,
    )
    decisions = [
        DomainDecision(
            paper_id="2605.00001v1",
            is_in_domain=True,
            confidence=0.91,
            decision="accept",
            reason="directly relevant",
            matched_concepts=["La3Ni2O7"],
        ),
        DomainDecision(
            paper_id="2605.00002v1",
            is_in_domain=False,
            confidence=0.7,
            decision="reject",
            reason="off topic",
        ),
    ]
    accepted.domain_decision = decisions[0]
    rejected.domain_decision = decisions[1]

    result = export_capture_artifacts(
        accepted_papers=[accepted],
        candidate_papers=[accepted, rejected],
        domain_decisions=decisions,
        config=_config(tmp_path),
        run_date="2026-05-22",
        report={"retrieved_count": 2, "longlisted_count": 2, "displayed_count": 1},
    )

    capture_dir = tmp_path / "capture"
    records = [json.loads(line) for line in (capture_dir / "papers.jsonl").read_text(encoding="utf-8").splitlines()]
    rejected_records = [
        json.loads(line)
        for line in (capture_dir / "rejected_candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    run_report = json.loads((capture_dir / "runs" / "2026-05-22.json").read_text(encoding="utf-8"))
    meta = json.loads((capture_dir / "fulltext" / "arxiv" / "2605.00001v1.meta.json").read_text(encoding="utf-8"))

    assert result.captured_count == 1
    assert records[0]["paper_id"] == "2605.00001v1"
    assert records[0]["text"]["full_text_path"].endswith("2605.00001v1.txt")
    assert records[0]["domain_decision"]["decision"] == "accept"
    assert rejected_records[0]["paper_id"] == "2605.00002v1"
    assert run_report["captured_count"] == 1
    assert meta["full_text_source"] == "html"
    assert meta["full_text_available"] is True
    assert (capture_dir / "fulltext" / "arxiv" / "2605.00001v1.txt").read_text(encoding="utf-8") == "Full accepted text"
    assert (capture_dir / "fulltext" / "arxiv" / "2605.00001v1.pdf").read_bytes() == b"%PDF accepted"


def test_export_capture_artifacts_uses_abstract_fallback_when_full_text_missing(tmp_path):
    paper = make_sample_paper(
        arxiv_id="2605.00003v1",
        abstract="Abstract fallback",
        full_text=None,
        full_text_source=None,
    )
    decision = DomainDecision(
        paper_id="2605.00003v1",
        is_in_domain=True,
        confidence=0.8,
        decision="accept",
        reason="accepted",
    )
    paper.domain_decision = decision

    export_capture_artifacts(
        accepted_papers=[paper],
        candidate_papers=[paper],
        domain_decisions=[decision],
        config=_config(tmp_path),
        run_date="2026-05-22",
        report={},
    )

    meta = json.loads((tmp_path / "capture" / "fulltext" / "arxiv" / "2605.00003v1.meta.json").read_text(encoding="utf-8"))
    text = (tmp_path / "capture" / "fulltext" / "arxiv" / "2605.00003v1.txt").read_text(encoding="utf-8")

    assert text == "Abstract fallback"
    assert meta["full_text_available"] is False
    assert meta["used_abstract_fallback"] is True


def test_export_capture_artifacts_extracts_text_from_pdf_when_only_pdf_is_available(tmp_path, monkeypatch):
    paper = make_sample_paper(
        arxiv_id="2605.00004v1",
        abstract="Abstract fallback",
        full_text=None,
        full_text_source=None,
        pdf_bytes=b"%PDF accepted only",
    )
    decision = DomainDecision(
        paper_id="2605.00004v1",
        is_in_domain=True,
        confidence=0.8,
        decision="accept",
        reason="accepted",
    )
    paper.domain_decision = decision
    extracted_pdf_bytes = []

    def fake_extract_markdown_from_pdf(path):
        extracted_pdf_bytes.append(Path(path).read_bytes())
        return "PDF extracted text"

    monkeypatch.setattr(
        capture_exporter,
        "extract_markdown_from_pdf",
        fake_extract_markdown_from_pdf,
        raising=False,
    )

    export_capture_artifacts(
        accepted_papers=[paper],
        candidate_papers=[paper],
        domain_decisions=[decision],
        config=_config(tmp_path),
        run_date="2026-05-22",
        report={},
    )

    meta = json.loads((tmp_path / "capture" / "fulltext" / "arxiv" / "2605.00004v1.meta.json").read_text(encoding="utf-8"))
    text = (tmp_path / "capture" / "fulltext" / "arxiv" / "2605.00004v1.txt").read_text(encoding="utf-8")

    assert text == "PDF extracted text"
    assert extracted_pdf_bytes == [b"%PDF accepted only"]
    assert meta["full_text_source"] == "pdf"
    assert meta["full_text_available"] is True
    assert meta["used_abstract_fallback"] is False


def test_export_capture_artifacts_upserts_domain_decisions_across_runs(tmp_path):
    first = make_sample_paper(arxiv_id="2605.00001v1", title="First")
    second = make_sample_paper(arxiv_id="2605.00002v1", title="Second")
    first_decision = DomainDecision(
        paper_id="2605.00001v1",
        is_in_domain=True,
        confidence=0.8,
        decision="accept",
        reason="first",
        accepted=True,
    )
    second_decision = DomainDecision(
        paper_id="2605.00002v1",
        is_in_domain=False,
        confidence=0.7,
        decision="reject",
        reason="second",
    )
    first.domain_decision = first_decision
    second.domain_decision = second_decision

    export_capture_artifacts(
        accepted_papers=[first],
        candidate_papers=[first],
        domain_decisions=[first_decision],
        config=_config(tmp_path),
        run_date="2026-05-21",
        report={},
    )
    export_capture_artifacts(
        accepted_papers=[],
        candidate_papers=[second],
        domain_decisions=[second_decision],
        config=_config(tmp_path),
        run_date="2026-05-22",
        report={},
    )

    decisions = json.loads((tmp_path / "capture" / "domain_decisions.json").read_text(encoding="utf-8"))

    assert {decision["paper_id"] for decision in decisions} == {"2605.00001v1", "2605.00002v1"}
