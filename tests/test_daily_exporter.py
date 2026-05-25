import json
from datetime import datetime, timezone
from pathlib import Path

from omegaconf import OmegaConf

from tests.canned_responses import make_sample_paper
from zotero_arxiv_daily2markdown.daily_exporter import (
    build_daily_payload,
    export_daily_json,
    normalize_arxiv_identity,
    resolve_daily_output_dir,
    title_plain,
)
from zotero_arxiv_daily2markdown.protocol import DomainDecision


def _config(tmp_path):
    return OmegaConf.create(
        {
            "domain": {"topic": "nickelate superconductors"},
            "prompt": {"topic": "fallback topic"},
            "hugo": {"output_dir": str(tmp_path / "site" / "content"), "auto_push": False},
        }
    )


def test_normalize_arxiv_identity_strips_version_from_page_key():
    assert normalize_arxiv_identity("https://arxiv.org/abs/2605.20653v1") == (
        "2605.20653",
        "v1",
        "2605.20653v1",
    )
    assert normalize_arxiv_identity("http://arxiv.org/pdf/2605.20653v2.pdf") == (
        "2605.20653",
        "v2",
        "2605.20653v2",
    )


def test_title_plain_removes_html_xml_and_mathml():
    title = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>R</mi></math> &amp; nickelates'

    plain = title_plain(title)

    assert plain == "R & nickelates"
    assert "<math" not in plain
    assert "</math>" not in plain
    assert "xmlns" not in plain


def test_export_daily_json_writes_all_accepted_papers_to_hugo_data_dir(tmp_path):
    config = _config(tmp_path)
    decision = DomainDecision(
        paper_id="2605.20653v1",
        is_in_domain=True,
        confidence=0.95,
        decision="accept",
        reason="direct nickelate superconductivity",
        matched_concepts=["nickelate"],
        accepted=True,
    )
    paper = make_sample_paper(
        arxiv_id="2605.20653v1",
        title='Pressure in <math xmlns="x"><mi>R</mi></math> nickelates',
        url="http://arxiv.org/abs/2605.20653v1",
        pdf_url="http://arxiv.org/pdf/2605.20653v1",
        tldr="中文总结",
        tldr_en="English summary",
        score=5.5961,
        categories=["cond-mat.supr-con"],
        primary_category="cond-mat.supr-con",
        affiliations=["The University of Tokyo"],
        published_at=datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc),
        full_text_path="data/capture/fulltext/arxiv/2605.20653.txt",
        pdf_path="data/capture/fulltext/arxiv/2605.20653.pdf",
        text_sha256="text-hash",
        pdf_sha256="pdf-hash",
        full_text_source="html",
    )
    paper.domain_decision = decision

    path = export_daily_json(
        accepted_papers=[paper],
        display_papers=[],
        candidate_papers=[paper],
        domain_decisions=[decision],
        overview_zh="中文总览",
        overview_en="English overview",
        config=config,
        announcement_date="2026-05-21",
        processed_at="2026-05-22T20:00:00+08:00",
        report={
            "retrieved_count": 42,
            "longlisted_count": 30,
            "accepted_count": 1,
            "rejected_count": 24,
            "uncertain_count": 3,
            "displayed_count": 0,
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["papers"][0]

    assert path == tmp_path / "site" / "data" / "daily" / "2026-05-21.json"
    assert payload["schema_version"] == "1.0"
    assert payload["announcement_date"] == "2026-05-21"
    assert payload["processed_at"] == "2026-05-22T20:00:00+08:00"
    assert payload["generated_at"].endswith("+08:00")
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["candidate_count"] == 42
    assert payload["displayed_count"] == 0
    assert payload["empty"] is False
    assert record["paper_id"] == "2605.20653"
    assert record["arxiv_id"] == "2605.20653"
    assert record["arxiv_version"] == "v1"
    assert record["source_ids"]["arxiv"] == "2605.20653v1"
    assert record["abs_url"] == "https://arxiv.org/abs/2605.20653v1"
    assert record["pdf_url"] == "https://arxiv.org/pdf/2605.20653v1"
    assert record["title_plain"] == "Pressure in R nickelates"
    assert record["summary_status"] == {"zh": "machine_generated", "en": "machine_generated"}
    assert record["domain_decision"]["decision"] == "accept"
    assert record["source_metadata"]["full_text_path"].endswith("2605.20653.txt")


def test_build_daily_payload_empty_report_has_no_papers(tmp_path):
    payload = build_daily_payload(
        accepted_papers=[],
        display_papers=[],
        candidate_papers=[],
        domain_decisions=[],
        overview_zh="",
        overview_en="",
        config=_config(tmp_path),
        announcement_date="2026-05-22",
        processed_at="2026-05-23T20:00:00+08:00",
        report={},
    )

    assert payload["empty"] is True
    assert payload["papers"] == []
    assert payload["overview"] == {"zh": None, "en": None}
    assert payload["arxiv_window"] is None
    assert payload["empty_reason"] == "No accepted papers matched the nickelate superconductors scope."


def test_resolve_daily_output_dir_falls_back_to_repo_local_data_dir():
    config = OmegaConf.create({})

    assert resolve_daily_output_dir(config) == Path("data/daily")
