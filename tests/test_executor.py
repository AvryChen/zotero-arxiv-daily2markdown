"""Tests for zotero_arxiv_daily2markdown.executor: normalize_path_patterns, filter_corpus, fetch_zotero_corpus, E2E."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily2markdown.executor import DailyRunResult, Executor, SingleDayArtifacts, expand_date_range, normalize_path_patterns
from zotero_arxiv_daily2markdown.protocol import CorpusPaper, DomainDecision


# ---------------------------------------------------------------------------
# normalize_path_patterns — migrated from test_include_path.py
# ---------------------------------------------------------------------------


def test_normalize_path_patterns_rejects_single_string_for_include_path():
    with pytest.raises(TypeError, match="config.zotero.include_path must be a list"):
        normalize_path_patterns("2026/survey/**", "include_path")


def test_normalize_path_patterns_accepts_list_config_for_include_path():
    include_path = OmegaConf.create(["2026/survey/**", "2026/reading-group/**"])
    assert normalize_path_patterns(include_path, "include_path") == [
        "2026/survey/**",
        "2026/reading-group/**",
    ]


def test_normalize_path_patterns_rejects_single_string_for_ignore_path():
    with pytest.raises(TypeError, match="config.zotero.ignore_path must be a list"):
        normalize_path_patterns("archive/**", "ignore_path")


def test_normalize_path_patterns_accepts_list_config_for_ignore_path():
    ignore_path = OmegaConf.create(["archive/**", "2025/**"])
    assert normalize_path_patterns(ignore_path, "ignore_path") == ["archive/**", "2025/**"]


def test_normalize_path_patterns_accepts_empty_list():
    assert normalize_path_patterns([], "ignore_path") == []


def test_normalize_path_patterns_accepts_none():
    assert normalize_path_patterns(None, "include_path") is None


# ---------------------------------------------------------------------------
# filter_corpus — migrated from test_include_path.py
# ---------------------------------------------------------------------------


def _make_executor(include_patterns=None, ignore_patterns=None):
    executor = Executor.__new__(Executor)
    executor.include_path_patterns = normalize_path_patterns(include_patterns, "include_path") if include_patterns else None
    executor.ignore_path_patterns = normalize_path_patterns(ignore_patterns, "ignore_path") if ignore_patterns else None
    return executor


def _accept_all_domain_papers(papers, config, client):
    decisions = []
    for paper in papers:
        decision = DomainDecision(
            paper_id=paper.arxiv_id or paper.url,
            is_in_domain=True,
            confidence=0.95,
            decision="accept",
            reason="test accepted paper",
            accepted=True,
        )
        paper.domain_decision = decision
        decisions.append(decision)
    return decisions


def _write_minimal_knowledge_output(output_dir, *, status="updated", output_files=None):
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paper_jsonl = "" if status == "empty_update" else '{"paper_id":"2605.00005","title":"Knowledge Paper"}\n'
    (output_dir / "papers.jsonl").write_text(paper_jsonl, encoding="utf-8")
    (output_dir / "all_papers.jsonl").write_text(paper_jsonl, encoding="utf-8")
    (output_dir / "domain_decisions.json").write_text("[]\n", encoding="utf-8")
    (output_dir / "paper_insights.json").write_text("[]\n", encoding="utf-8")
    (output_dir / "paper_workflows.json").write_text("[]\n", encoding="utf-8")
    (output_dir / "facet_vocabulary.json").write_text("[]\n", encoding="utf-8")
    (output_dir / "facet_vocabulary.csv").write_text("facet_type,canonical_name\n", encoding="utf-8")
    (output_dir / "materials_methods_matrix.csv").write_text("paper_id\n", encoding="utf-8")
    (output_dir / "build_report.json").write_text(
        json.dumps(
            {
                "mode": "incremental_daily_update",
                "status": status,
                "total_papers": 0 if status == "empty_update" else 1,
                "output_dir": str(output_dir),
                "output_files": output_files or [],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_filter_corpus_matches_any_path_against_any_pattern():
    executor = _make_executor(include_patterns=["2026/survey/**", "2026/reading-group/**"])
    corpus = [
        CorpusPaper(title="Survey Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a", "archive/misc"]),
        CorpusPaper(title="Reading Group Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["notes/inbox", "2026/reading-group/week-1"]),
        CorpusPaper(title="Excluded Paper", abstract="", added_date=datetime(2026, 1, 3), paths=["2025/other/topic"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Survey Paper", "Reading Group Paper"]


def test_filter_corpus_excludes_papers_matching_ignore_path():
    executor = _make_executor(ignore_patterns=["archive/**", "2025/**"])
    corpus = [
        CorpusPaper(title="Active Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a"]),
        CorpusPaper(title="Archived Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["archive/misc"]),
        CorpusPaper(title="Old Paper", abstract="", added_date=datetime(2026, 1, 3), paths=["2025/other/topic"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Active Paper"]


def test_filter_corpus_ignore_path_takes_precedence_over_include_path():
    executor = _make_executor(include_patterns=["2026/**"], ignore_patterns=["2026/ignore/**"])
    corpus = [
        CorpusPaper(title="Included Paper", abstract="", added_date=datetime(2026, 1, 1), paths=["2026/survey/topic-a"]),
        CorpusPaper(title="Ignored Paper", abstract="", added_date=datetime(2026, 1, 2), paths=["2026/ignore/topic-b"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert [p.title for p in filtered] == ["Included Paper"]


def test_filter_corpus_no_filters_returns_all():
    executor = _make_executor()
    corpus = [
        CorpusPaper(title="Paper A", abstract="", added_date=datetime(2026, 1, 1), paths=["foo"]),
        CorpusPaper(title="Paper B", abstract="", added_date=datetime(2026, 1, 2), paths=["bar"]),
    ]
    filtered = executor.filter_corpus(corpus)
    assert filtered == corpus


# ---------------------------------------------------------------------------
# fetch_zotero_corpus
# ---------------------------------------------------------------------------


def test_fetch_zotero_corpus(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 2
    assert corpus[0].title == "Stub Paper 1"
    assert "survey/topic-a" in corpus[0].paths[0]


def test_fetch_zotero_corpus_paper_with_zero_collections(config, monkeypatch):
    from tests.canned_responses import make_stub_zotero_client

    items = [
        {
            "data": {
                "title": "No Collection Paper",
                "abstractNote": "Abstract.",
                "dateAdded": "2026-03-01T00:00:00Z",
                "collections": [],
            }
        }
    ]
    stub_zot = make_stub_zotero_client(items=items)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 1
    assert corpus[0].paths == []


def test_fetch_zotero_corpus_retries_transport_errors(config, monkeypatch):
    import httpx
    from tests.canned_responses import make_stub_zotero_client

    attempts = 0
    waits = []
    stub_zot = make_stub_zotero_client()

    def make_client(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary DNS failure")
        return stub_zot

    config.executor.zotero_max_attempts = 4
    config.executor.zotero_retry_base_seconds = 2
    config.executor.zotero_retry_max_seconds = 10
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", make_client)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.time.sleep", waits.append)

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = executor.fetch_zotero_corpus()

    assert len(corpus) == 2
    assert attempts == 3
    assert waits == [2, 4]


def test_fetch_zotero_corpus_does_not_retry_programming_errors(config, monkeypatch):
    attempts = 0

    def make_client(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("bad response shape")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", make_client)

    executor = Executor.__new__(Executor)
    executor.config = config
    with pytest.raises(ValueError, match="bad response shape"):
        executor.fetch_zotero_corpus()

    assert attempts == 1


def test_executor_rejects_non_arxiv_sources(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.source = ["arxiv", "biorxiv"]

    with pytest.raises(ValueError, match="Only arxiv is supported"):
        Executor(config)


def test_expand_date_range_includes_both_endpoints():
    assert expand_date_range("2026-05-01", "2026-05-03") == [
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]


def test_expand_date_range_rejects_reverse_range():
    with pytest.raises(ValueError, match="start_date"):
        expand_date_range("2026-05-03", "2026-05-01")


def test_executor_rejects_target_date_with_date_range(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.target_date = "2026-05-01"
        config.executor.start_date = "2026-05-01"
        config.executor.end_date = "2026-05-03"

    executor = Executor.__new__(Executor)
    executor.config = config

    with pytest.raises(ValueError, match="target_date"):
        executor._get_date_range()


def test_executor_rejects_partial_date_range(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.start_date = "2026-05-01"
        config.executor.end_date = None

    executor = Executor.__new__(Executor)
    executor.config = config

    with pytest.raises(ValueError, match="configured together"):
        executor._get_date_range()


# ---------------------------------------------------------------------------
# E2E: Executor.run()
# ---------------------------------------------------------------------------


def test_run_end_to_end(config, monkeypatch):
    """Full pipeline: Zotero fetch -> filter -> retrieve -> rerank -> TLDR -> email."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import (
        make_sample_corpus,
        make_sample_paper,
        make_stub_openai_client,
        make_stub_smtp,
        make_stub_zotero_client,
    )

    # Config: source=["arxiv"], reranker="api", send_empty=false
    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False

    # 1. Stub pyzotero
    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    # 2. Stub OpenAI (for reranker + TLDR/affiliations)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.reranker.api.OpenAI", lambda **kw: stub_client)
    retrieved = [
        make_sample_paper(title="E2E Paper 1", score=None),
        make_sample_paper(title="E2E Paper 2", score=None),
    ]

    # Import to register the arxiv retriever
    import zotero_arxiv_daily2markdown.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily2markdown.retriever.base import registered_retrievers

    monkeypatch.setattr(
        registered_retrievers["arxiv"],
        "retrieve_papers",
        lambda self: retrieved,
    )

    # 4. Stub SMTP
    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    # 5. Stub sleep (reranker/retriever)

    # 6. Run
    executor = Executor(config)
    executor.run()

    # Assertions
    assert len(sent) == 1, "Email should have been sent"
    _, _, email_body = sent[0]
    assert "text/html" in email_body


def test_run_no_papers_send_empty_false(config, monkeypatch):
    """When no papers are found and send_empty=false, no email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = False

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.reranker.api.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily2markdown.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily2markdown.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    executor.run()

    assert len(sent) == 0, "No email should be sent when no papers and send_empty=false"


def test_run_no_papers_send_empty_true(config, monkeypatch):
    """When no papers are found and send_empty=true, empty email is sent."""
    import smtplib

    from omegaconf import open_dict

    from tests.canned_responses import make_stub_openai_client, make_stub_smtp, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.reranker = "api"
        config.executor.send_empty = True

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", lambda *a, **kw: stub_zot)

    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.reranker.api.OpenAI", lambda **kw: stub_client)

    import zotero_arxiv_daily2markdown.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily2markdown.retriever.base import registered_retrievers

    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: [])

    sent = []
    monkeypatch.setattr(smtplib, "SMTP", make_stub_smtp(sent))

    executor = Executor(config)
    executor.run()

    assert len(sent) == 1, "Email should be sent even with no papers when send_empty=true"
    _, _, body = sent[0]
    assert "text/html" in body


def test_run_fetches_full_text_only_after_domain_accepted_longlist(config, monkeypatch):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper, make_stub_openai_client, make_stub_zotero_client

    with open_dict(config):
        config.executor.source = ["arxiv"]
        config.executor.max_paper_num = 2
        config.executor.longlist = 3
        config.executor.score_threshold = 0.0
        config.executor.send_empty = False

    stub_zot = make_stub_zotero_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.zotero.Zotero", lambda *a, **kw: stub_zot)
    stub_client = make_stub_openai_client()
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.OpenAI", lambda **kw: stub_client)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.send_email", lambda *a, **kw: None)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.export_to_hugo", lambda *a, **kw: None)

    import zotero_arxiv_daily2markdown.retriever.arxiv_retriever  # noqa: F401

    from zotero_arxiv_daily2markdown.retriever.base import registered_retrievers

    retrieved = [
        make_sample_paper(title="Paper A", abstract="A", full_text=None),
        make_sample_paper(title="Paper B", abstract="B", full_text=None),
        make_sample_paper(title="Paper C", abstract="C", full_text=None),
    ]
    monkeypatch.setattr(registered_retrievers["arxiv"], "retrieve_papers", lambda self: retrieved)

    rerank_calls = []
    tldr_calls = []
    tldr_en_calls = []
    affiliation_calls = []

    class StubReranker:
        def rerank(
            self,
            candidates,
            corpus,
            *,
            include_full_text=True,
            include_tldr=False,
            include_english_tldr=False,
            max_full_text_chars=None,
        ):
            rerank_calls.append(
                (
                    include_full_text,
                    include_tldr,
                    include_english_tldr,
                    max_full_text_chars,
                    [p.title for p in candidates],
                    [p.full_text for p in candidates],
                    [p.tldr for p in candidates],
                    [p.tldr_en for p in candidates],
                )
            )
            if include_english_tldr:
                scores = {"Paper A": 1.0, "Paper B": 9.0, "Paper C": 8.5}
            else:
                scores = {"Paper A": 9.0, "Paper B": 8.0, "Paper C": 7.0}
            for paper in candidates:
                paper.score = scores[paper.title]
            return sorted(candidates, key=lambda paper: paper.score, reverse=True)

    executor = Executor(config)
    executor.reranker = StubReranker()

    enriched = []

    def populate_full_text(self, paper):
        paper.full_text = f"FULL {paper.title}"
        enriched.append(paper.title)
        return paper

    monkeypatch.setattr(type(executor.retrievers["arxiv"]), "populate_full_text", populate_full_text)

    def generate_tldr(self, openai_client, llm_params):
        if self.tldr:
            return self.tldr
        self.tldr = f"ZH {self.title}"
        tldr_calls.append(self.title)
        return self.tldr

    def generate_english_tldr(self, openai_client, llm_params):
        if self.tldr_en:
            return self.tldr_en
        self.tldr_en = f"EN {self.title}"
        tldr_en_calls.append(self.title)
        return self.tldr_en

    def generate_affiliations(self, openai_client, llm_params):
        affiliation_calls.append(self.title)
        self.affiliations = [f"Affiliation {self.title}"]
        return self.affiliations

    monkeypatch.setattr("zotero_arxiv_daily2markdown.protocol.Paper.generate_tldr", generate_tldr)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.protocol.Paper.generate_english_tldr", generate_english_tldr)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.protocol.Paper.generate_affiliations", generate_affiliations)

    executor.run()

    assert rerank_calls[0][0] is False
    assert rerank_calls[0][1] is False
    assert rerank_calls[0][2] is False
    assert rerank_calls[0][3] is None
    assert rerank_calls[0][4] == ["Paper A", "Paper B", "Paper C"]
    assert rerank_calls[0][5] == [None, None, None]
    assert len(rerank_calls) == 1
    assert enriched == ["Paper A", "Paper B", "Paper C"]
    assert sorted(tldr_calls) == ["Paper A", "Paper B", "Paper C"]
    assert sorted(tldr_en_calls) == ["Paper A", "Paper B", "Paper C"]
    assert affiliation_calls == ["Paper A", "Paper B", "Paper C"]


def test_build_single_day_captures_all_accepted_but_displays_top_limit(config, monkeypatch, tmp_path):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.executor.target_date = "2026-05-21"
        config.executor.max_paper_num = 2
        config.executor.longlist = 3
        config.executor.score_threshold = 0.0
        config.domain.use_ai = False
        config.capture.enabled = True
        config.capture.output_dir = str(tmp_path / "capture")
        config.capture.fulltext_dir = str(tmp_path / "capture" / "fulltext")
        config.display.max_paper_num = 2
        config.hugo.output_dir = str(tmp_path / "site" / "content")

    retrieved = [
        make_sample_paper(title="Paper A", arxiv_id="2605.00001v1", full_text=None),
        make_sample_paper(title="Paper B", arxiv_id="2605.00002v1", full_text=None),
        make_sample_paper(title="Paper C", arxiv_id="2605.00003v1", full_text=None),
        make_sample_paper(title="Paper D", arxiv_id="2605.00004v1", full_text=None),
    ]

    class StubRetriever:
        fetch_full_text_during_retrieval = False

        def retrieve_papers(self):
            return retrieved

        def populate_full_text(self, paper):
            paper.full_text = f"FULL {paper.title}"
            paper.full_text_source = "html"
            return paper

    class StubReranker:
        def rerank(self, candidates, corpus, *, include_full_text=True, **kwargs):
            scores = {"Paper A": 9.0, "Paper B": 8.0, "Paper C": 7.0, "Paper D": 6.0}
            for paper in candidates:
                paper.score = scores[paper.title]
            return sorted(candidates, key=lambda paper: paper.score, reverse=True)

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.retrievers = {"arxiv": StubRetriever()}
    executor.reranker = StubReranker()
    executor.openai_client = object()

    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.protocol.Paper.generate_tldr",
        lambda self, client, params: setattr(self, "tldr", f"ZH {self.title}") or self.tldr,
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.protocol.Paper.generate_english_tldr",
        lambda self, client, params: setattr(self, "tldr_en", f"EN {self.title}") or self.tldr_en,
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.protocol.Paper.generate_affiliations",
        lambda self, client, params: setattr(self, "affiliations", [self.title]) or self.affiliations,
    )

    artifacts = executor._build_single_day_artifacts([])

    records = (tmp_path / "capture" / "papers.jsonl").read_text(encoding="utf-8").splitlines()
    daily_payload = json.loads((tmp_path / "site" / "data" / "daily" / "2026-05-21.json").read_text(encoding="utf-8"))
    assert [paper.title for paper in artifacts.accepted_papers] == ["Paper A", "Paper B", "Paper C"]
    assert [paper.title for paper in artifacts.papers] == ["Paper A", "Paper B"]
    assert artifacts.result.accepted_count == 3
    assert artifacts.result.displayed_count == 2
    assert len(records) == 3
    assert len(daily_payload["papers"]) == 3
    assert daily_payload["displayed_count"] == 2
    assert daily_payload["papers"][0]["paper_id"] == "2605.00001"


def test_build_single_day_updates_knowledge_after_capture(config, monkeypatch, tmp_path):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.executor.target_date = "2026-05-21"
        config.executor.score_threshold = 0.0
        config.executor.send_empty = True
        config.domain.use_ai = False
        config.capture.enabled = True
        config.capture.output_dir = str(tmp_path / "capture")
        config.capture.fulltext_dir = str(tmp_path / "capture" / "fulltext")
        config.hugo.output_dir = str(tmp_path / "site" / "content")
        config.knowledge = {
            "enabled": True,
            "output_dir": str(tmp_path / "site" / "data" / "knowledge"),
            "batch_size": 16,
            "full_text_char_budget": 120000,
            "non_blocking": True,
            "run_alignment": True,
            "run_vocabulary_review": True,
        }

    paper = make_sample_paper(title="Knowledge Paper", arxiv_id="2605.00005v1", full_text="Full text")
    paper.score = 5.0
    paper.pdf_bytes = b"%PDF knowledge"

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.debug = False
    executor.retrievers = {"arxiv": SimpleNamespace(retrieve_papers=lambda: [paper], populate_full_text=lambda p: None)}
    executor.reranker = SimpleNamespace(rerank=lambda candidates, corpus, **kwargs: candidates)
    executor.openai_client = SimpleNamespace()
    executor._prepared_rerank_corpus = None

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.classify_domain_papers", _accept_all_domain_papers)
    captured_options = []

    def fake_update(options):
        captured_options.append(options)
        _write_minimal_knowledge_output(options.output_dir)
        return {"mode": "incremental_daily_update"}

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.update_knowledge_base_incremental", fake_update)

    artifacts = executor._build_single_day_artifacts([])

    assert artifacts.result.knowledge_error is None
    assert str(tmp_path / "site" / "data" / "knowledge" / "papers.jsonl") in artifacts.knowledge_paths
    assert str(tmp_path / "site" / "data" / "knowledge" / "aligned_vocabulary.json") in artifacts.knowledge_paths
    assert captured_options[0].capture_dirs == [str(tmp_path / "capture")]
    assert captured_options[0].output_dir != str(tmp_path / "site" / "data" / "knowledge")
    assert captured_options[0].accepted_paper_ids is None
    assert captured_options[0].run_report_path == str(tmp_path / "capture" / "runs" / "2026-05-21.json")
    assert captured_options[0].announcement_date == "2026-05-21"
    assert captured_options[0].batch_size == 16
    build_report = json.loads((tmp_path / "site" / "data" / "knowledge" / "build_report.json").read_text(encoding="utf-8"))
    assert build_report["output_dir"] == str(tmp_path / "site" / "data" / "knowledge")


def test_build_single_day_records_non_blocking_knowledge_error(config, monkeypatch, tmp_path):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.executor.target_date = "2026-05-21"
        config.executor.score_threshold = 0.0
        config.executor.send_empty = True
        config.domain.use_ai = False
        config.capture.enabled = True
        config.capture.output_dir = str(tmp_path / "capture")
        config.capture.fulltext_dir = str(tmp_path / "capture" / "fulltext")
        config.hugo.output_dir = str(tmp_path / "site" / "content")
        config.knowledge = {"enabled": True, "output_dir": str(tmp_path / "site" / "data" / "knowledge"), "non_blocking": True}

    paper = make_sample_paper(title="Knowledge Paper", arxiv_id="2605.00006v1", full_text="Full text")
    paper.score = 5.0

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.debug = False
    executor.retrievers = {"arxiv": SimpleNamespace(retrieve_papers=lambda: [paper], populate_full_text=lambda p: None)}
    executor.reranker = SimpleNamespace(rerank=lambda candidates, corpus, **kwargs: candidates)
    executor.openai_client = SimpleNamespace()
    executor._prepared_rerank_corpus = None

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.classify_domain_papers", _accept_all_domain_papers)
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.update_knowledge_base_incremental",
        lambda options: (_ for _ in ()).throw(RuntimeError("knowledge failed")),
    )

    artifacts = executor._build_single_day_artifacts([])

    assert artifacts.result.accepted_count == 1
    assert artifacts.result.knowledge_error == "knowledge failed"
    assert artifacts.knowledge_paths == []


def test_build_single_day_skips_knowledge_when_disabled(config, monkeypatch, tmp_path):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.executor.target_date = "2026-05-21"
        config.executor.score_threshold = 0.0
        config.executor.send_empty = True
        config.domain.use_ai = False
        config.capture.enabled = True
        config.capture.output_dir = str(tmp_path / "capture")
        config.capture.fulltext_dir = str(tmp_path / "capture" / "fulltext")
        config.hugo.output_dir = str(tmp_path / "site" / "content")
        config.knowledge = {"enabled": False, "output_dir": str(tmp_path / "site" / "data" / "knowledge")}

    paper = make_sample_paper(title="Knowledge Paper", arxiv_id="2605.00007v1", full_text="Full text")
    paper.score = 5.0

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.debug = False
    executor.retrievers = {"arxiv": SimpleNamespace(retrieve_papers=lambda: [paper], populate_full_text=lambda p: None)}
    executor.reranker = SimpleNamespace(rerank=lambda candidates, corpus, **kwargs: candidates)
    executor.openai_client = SimpleNamespace()
    executor._prepared_rerank_corpus = None

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.classify_domain_papers", _accept_all_domain_papers)
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.update_knowledge_base_incremental",
        lambda options: (_ for _ in ()).throw(AssertionError("knowledge should be disabled")),
    )

    artifacts = executor._build_single_day_artifacts([])

    assert artifacts.result.knowledge_error is None
    assert artifacts.knowledge_paths == []


def test_build_single_day_runs_empty_knowledge_update_when_no_papers_accepted(config, monkeypatch, tmp_path):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.executor.target_date = "2026-05-21"
        config.executor.score_threshold = 10.0
        config.executor.send_empty = False
        config.capture.enabled = True
        config.capture.output_dir = str(tmp_path / "capture")
        config.capture.fulltext_dir = str(tmp_path / "capture" / "fulltext")
        config.hugo.output_dir = str(tmp_path / "site" / "content")
        config.knowledge = {"enabled": True, "output_dir": str(tmp_path / "site" / "data" / "knowledge"), "non_blocking": True}

    paper = make_sample_paper(title="Below threshold", arxiv_id="2605.00008v1", full_text="Full text")
    paper.score = 1.0

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.debug = False
    executor.retrievers = {"arxiv": SimpleNamespace(retrieve_papers=lambda: [paper], populate_full_text=lambda p: None)}
    executor.reranker = SimpleNamespace(rerank=lambda candidates, corpus, **kwargs: candidates)
    executor.openai_client = SimpleNamespace()
    executor._prepared_rerank_corpus = None
    captured_options = []

    def fake_update(options):
        captured_options.append(options)
        _write_minimal_knowledge_output(options.output_dir, status="empty_update")
        return {"mode": "incremental_daily_update", "status": "empty_update"}

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.update_knowledge_base_incremental", fake_update)

    artifacts = executor._build_single_day_artifacts([])

    assert artifacts.result.accepted_count == 0
    assert artifacts.result.captured_count == 0
    assert artifacts.result.knowledge_updated is True
    assert artifacts.result.knowledge_error is None
    assert captured_options[0].accepted_paper_ids is None
    assert captured_options[0].run_report_path == str(tmp_path / "capture" / "runs" / "2026-05-21.json")
    assert captured_options[0].announcement_date == "2026-05-21"
    assert (tmp_path / "site" / "data" / "knowledge" / "aligned_vocabulary.json").exists()
    assert artifacts.knowledge_paths == []


def test_atomic_knowledge_update_preserves_existing_output_on_empty_update(monkeypatch, tmp_path):
    from arxiv_knowledge_builder import IncrementalUpdateOptions

    output_dir = tmp_path / "knowledge"
    _write_minimal_knowledge_output(output_dir, status="updated")
    original_papers = (output_dir / "papers.jsonl").read_text(encoding="utf-8")
    original_report = (output_dir / "build_report.json").read_text(encoding="utf-8")

    executor = Executor.__new__(Executor)

    def fake_update(options):
        _write_minimal_knowledge_output(options.output_dir, status="empty_update")

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.update_knowledge_base_incremental", fake_update)

    report = executor._run_atomic_knowledge_update(
        IncrementalUpdateOptions(capture_dirs=[], output_dir=str(output_dir))
    )

    assert report["status"] == "empty_update"
    assert (output_dir / "papers.jsonl").read_text(encoding="utf-8") == original_papers
    assert (output_dir / "build_report.json").read_text(encoding="utf-8") == original_report


def test_executor_defaults_longlist_to_one_point_five_x_max(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.max_paper_num = 20
        config.executor.longlist = None

    executor = Executor.__new__(Executor)
    executor.config = config

    assert executor._resolve_longlist_size() == 30


def test_generate_longlist_summaries_uses_configured_concurrency(config, monkeypatch):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.llm_concurrency = 4

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.openai_client = object()

    papers = [
        SimpleNamespace(
            title=f"Paper {i}",
            generate_tldr=lambda openai_client, llm_params: None,
            generate_english_tldr=lambda openai_client, llm_params: None,
        )
        for i in range(3)
    ]

    captured = {"max_workers": None, "submitted": 0}

    class InlineFuture:
        def result(self):
            return None

    class InlinePool:
        def __init__(self, max_workers):
            captured["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, paper):
            captured["submitted"] += 1
            fn(paper)
            return InlineFuture()

    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.ThreadPoolExecutor", InlinePool)
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.as_completed", lambda futures: iter(futures))

    executor._generate_longlist_summaries(papers)

    assert captured["max_workers"] == 3
    assert captured["submitted"] == 3


def test_run_date_range_runs_each_day_without_email_by_default(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.start_date = "2026-05-01"
        config.executor.end_date = "2026-05-03"
        config.executor.historical_mode = "export_only"

    executor = Executor.__new__(Executor)
    executor.config = config
    calls = []

    def run_single_day(corpus, *, send_email_enabled=True):
        calls.append((config.executor.target_date, send_email_enabled, list(corpus)))
        return SimpleNamespace(
            target_date=config.executor.target_date,
            skipped=False,
            error=None,
        )

    executor._run_single_day = run_single_day
    corpus = [CorpusPaper(title="C", abstract="A", added_date=datetime(2026, 1, 1), paths=[])]

    results = executor._run_date_range(["2026-05-01", "2026-05-02", "2026-05-03"], corpus)

    assert [call[0] for call in calls] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert [call[1] for call in calls] == [False, False, False]
    assert [result.target_date for result in results] == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert config.executor.target_date is None


def test_run_prepares_rerank_corpus_once_for_historical_range(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.start_date = "2026-05-01"
        config.executor.end_date = "2026-05-03"

    executor = Executor.__new__(Executor)
    executor.config = config
    corpus = [CorpusPaper(title="C", abstract="A", added_date=datetime(2026, 1, 1), paths=[])]
    prepared = object()
    prepare_calls = []
    range_calls = []

    class StubReranker:
        supports_prepared_corpus = True

        def prepare_corpus(self, items):
            prepare_calls.append(list(items))
            return prepared

    executor.reranker = StubReranker()
    executor.fetch_zotero_corpus = lambda: corpus
    executor.filter_corpus = lambda items: items

    def run_date_range(dates, items):
        range_calls.append((dates, items, executor._prepared_rerank_corpus))
        return []

    executor._run_date_range = run_date_range

    executor.run()

    assert prepare_calls == [corpus]
    assert range_calls == [(["2026-05-01", "2026-05-02", "2026-05-03"], corpus, prepared)]
    assert executor._prepared_rerank_corpus is None


def test_build_single_day_uses_prepared_rerank_corpus_when_available(config, tmp_path):
    from omegaconf import open_dict

    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.executor.target_date = "2026-05-21"
        config.executor.score_threshold = 99.0
        config.capture.enabled = False
        config.hugo.output_dir = str(tmp_path / "site" / "content")

    paper = make_sample_paper(title="Candidate")
    prepared = object()
    rerank_calls = []

    class StubRetriever:
        fetch_full_text_during_retrieval = False

        def retrieve_papers(self):
            return [paper]

    class StubReranker:
        supports_prepared_corpus = True

        def rerank(self, candidates, corpus, *, include_full_text=True, prepared_corpus=None, **kwargs):
            rerank_calls.append((include_full_text, prepared_corpus, list(candidates), list(corpus)))
            candidates[0].score = 1.0
            return candidates

    executor = Executor.__new__(Executor)
    executor.config = config
    executor.retrievers = {"arxiv": StubRetriever()}
    executor.reranker = StubReranker()
    executor._prepared_rerank_corpus = prepared
    executor.openai_client = object()

    corpus = [CorpusPaper(title="C", abstract="A", added_date=datetime(2026, 1, 1), paths=[])]
    artifacts = executor._build_single_day_artifacts(corpus)

    assert artifacts.result.retrieved_count == 1
    assert rerank_calls == [(False, prepared, [paper], corpus)]
    daily_payload = json.loads((tmp_path / "site" / "data" / "daily" / "2026-05-21.json").read_text(encoding="utf-8"))
    assert daily_payload["empty"] is True
    assert daily_payload["papers"] == []


def test_run_date_range_cools_down_between_processed_dates(config, monkeypatch):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.historical_mode = "export_only"
        config.executor.historical_day_cooldown_seconds = 12

    executor = Executor.__new__(Executor)
    executor.config = config
    sleeps = []
    calls = []

    def run_single_day(corpus, *, send_email_enabled=True):
        calls.append(config.executor.target_date)
        return SimpleNamespace(target_date=config.executor.target_date, skipped=False, error=None)

    executor._run_single_day = run_single_day
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.time.sleep", lambda seconds: sleeps.append(seconds))

    executor._run_date_range(["2026-05-01", "2026-05-02", "2026-05-03"], [])

    assert calls == ["2026-05-01", "2026-05-02", "2026-05-03"]
    assert sleeps == [12, 12]


def test_run_single_day_sends_error_email_on_failure(config, monkeypatch):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.error_email_enabled = True

    sent = []
    executor = Executor.__new__(Executor)
    executor.config = config
    executor.retrievers = {
        "arxiv": SimpleNamespace(
            last_fetch_report=SimpleNamespace(summary=lambda: "source=arxiv mode=rss expected=1 fetched=0")
        )
    }

    def broken_impl(corpus, *, send_email_enabled=True, empty_notice_on_skip=False):
        raise RuntimeError("arxiv unavailable")

    executor._run_single_day_impl = broken_impl
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.send_email",
        lambda config, body, subject=None: sent.append((subject, body)),
    )

    with pytest.raises(RuntimeError, match="arxiv unavailable"):
        executor._run_single_day([])

    assert sent
    assert sent[0][0] == "arXiv Daily failed: latest"
    assert "arxiv unavailable" in sent[0][1]
    assert "source=arxiv mode=rss expected=1 fetched=0" in sent[0][1]


def test_run_single_day_email_failure_still_exports(config, monkeypatch):
    from tests.canned_responses import make_sample_paper

    paper = make_sample_paper()
    executor = Executor.__new__(Executor)
    executor.config = config
    executor._build_single_day_artifacts = lambda corpus: SingleDayArtifacts(
        result=DailyRunResult(target_date=None, retrieved_count=1, selected_count=1),
        papers=[paper],
        overview_zh="overview zh",
        overview_en="overview en",
    )

    export_calls = []
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.send_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("smtp timed out")),
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.export_to_hugo",
        lambda *args, **kwargs: export_calls.append(args),
    )

    result = executor._run_single_day([])

    assert result.emailed is False
    assert result.exported is True
    assert len(export_calls) == 1
    assert export_calls[0][0] == [paper]


def test_run_single_day_exports_daily_and_knowledge_extra_paths(config, monkeypatch):
    from tests.canned_responses import make_sample_paper

    paper = make_sample_paper()
    executor = Executor.__new__(Executor)
    executor.config = config
    executor._build_single_day_artifacts = lambda corpus: SingleDayArtifacts(
        result=DailyRunResult(target_date="2026-05-21", retrieved_count=1, selected_count=1),
        papers=[paper],
        overview_zh="overview zh",
        overview_en="overview en",
        daily_json_path="/site/data/daily/2026-05-21.json",
        knowledge_paths=["/site/data/knowledge/papers.jsonl", "/site/data/knowledge/build_report.json"],
    )

    export_calls = []
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.export_to_hugo",
        lambda *args, **kwargs: export_calls.append((args, kwargs)),
    )

    result = executor._run_single_day([])

    assert result.exported is True
    assert export_calls[0][1]["extra_paths"] == [
        "/site/data/daily/2026-05-21.json",
        "/site/data/knowledge/papers.jsonl",
        "/site/data/knowledge/build_report.json",
    ]


def test_run_date_range_can_send_email(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.historical_mode = "email_and_export"

    executor = Executor.__new__(Executor)
    executor.config = config
    send_flags = []

    def run_single_day(corpus, *, send_email_enabled=True):
        send_flags.append(send_email_enabled)
        return SimpleNamespace(target_date=config.executor.target_date, skipped=False, error=None)

    executor._run_single_day = run_single_day
    executor._run_date_range(["2026-05-01"], [])

    assert send_flags == [True]


def test_run_default_daily_processes_yesterday_target_and_cleans_empty_notices(config):
    executor = Executor.__new__(Executor)
    executor.config = config
    calls = []
    corpus = [CorpusPaper(title="C", abstract="A", added_date=datetime(2026, 1, 1), paths=[])]

    executor.fetch_zotero_corpus = lambda: corpus
    executor.filter_corpus = lambda items: items
    executor._correction_target_date = lambda: "2026-05-21"
    executor._cleanup_empty_hugo_notices = lambda: calls.append(("cleanup", config.executor.target_date))

    def run_single_day(corpus, *, send_email_enabled=True, empty_notice_on_skip=False):
        calls.append((config.executor.target_date, send_email_enabled, empty_notice_on_skip, list(corpus)))
        return DailyRunResult(target_date=config.executor.target_date)

    executor._run_single_day = run_single_day

    executor.run()

    assert calls == [
        ("cleanup", None),
        ("2026-05-21", True, True, corpus),
    ]
    assert config.executor.target_date is None


def test_run_does_not_trigger_previous_day_correction_in_historical_mode(config, monkeypatch):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.start_date = "2026-05-01"
        config.executor.end_date = "2026-05-03"

    executor = Executor.__new__(Executor)
    executor.config = config
    calls = []
    corpus = [CorpusPaper(title="C", abstract="A", added_date=datetime(2026, 1, 1), paths=[])]

    executor.fetch_zotero_corpus = lambda: corpus
    executor.filter_corpus = lambda items: items
    executor._run_date_range = lambda dates, corpus: calls.append(("date_range", dates)) or []
    executor._apply_previous_day_correction = lambda correction_corpus: calls.append(("correction", correction_corpus))

    executor.run()

    assert calls == [("date_range", ["2026-05-01", "2026-05-02", "2026-05-03"])]


def test_run_single_day_exports_empty_notice_for_default_daily_empty_result(config, monkeypatch):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.send_empty = False
        config.executor.target_date = "2026-05-21"

    executor = Executor.__new__(Executor)
    executor.config = config
    result = DailyRunResult(target_date="2026-05-21", retrieved_count=5, selected_count=0)
    executor._build_single_day_artifacts = lambda corpus: SingleDayArtifacts(
        result=result,
        papers=[],
        overview_zh="",
        overview_en="",
    )

    notice_calls = []
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.export_empty_notice_to_hugo",
        lambda cfg, extra_paths=None: notice_calls.append((cfg.executor.target_date, extra_paths)) or object(),
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.export_to_hugo",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("normal Hugo export should not run")),
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.send_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("empty email should not be sent")),
    )

    returned = executor._run_single_day([], send_email_enabled=True, empty_notice_on_skip=True)

    assert returned.skipped is True
    assert returned.exported is True
    assert returned.emailed is False
    assert notice_calls == [("2026-05-21", None)]


def test_previous_day_correction_skips_when_hugo_urls_match(config, monkeypatch, tmp_path):
    from omegaconf import open_dict
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily2markdown.executor import SingleDayArtifacts

    with open_dict(config):
        config.hugo.output_dir = str(tmp_path)

    executor = Executor.__new__(Executor)
    executor.config = config
    executor._correction_target_date = lambda: "2026-05-19"
    executor._build_single_day_artifacts = lambda corpus: SingleDayArtifacts(
        result=DailyRunResult(target_date="2026-05-19"),
        papers=[make_sample_paper(url="https://arxiv.org/abs/2605.00001v1")],
        overview_zh="overview zh",
        overview_en="overview en",
    )

    zh_path = tmp_path / "zh" / "posts" / "2026-05-19-arxiv-daily.md"
    en_path = tmp_path / "en" / "posts" / "2026-05-19-arxiv-daily.md"
    zh_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.parent.mkdir(parents=True, exist_ok=True)
    zh_path.write_text(
        "- **Link**: [https://arxiv.org/abs/2605.00001v1](https://arxiv.org/abs/2605.00001v1)\n",
        encoding="utf-8",
    )
    en_path.write_text(
        "- **Link**: [https://arxiv.org/abs/2605.00001v1](https://arxiv.org/abs/2605.00001v1)\n",
        encoding="utf-8",
    )

    export_calls = []
    email_calls = []
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.export_to_hugo", lambda *args, **kwargs: export_calls.append(args))
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.send_email",
        lambda config, html, subject=None: email_calls.append((subject, html)),
    )

    executor._apply_previous_day_correction()

    assert export_calls == []
    assert email_calls == []


def test_previous_day_correction_overwrites_and_emails_when_hugo_urls_change(config, monkeypatch, tmp_path):
    from omegaconf import open_dict
    from tests.canned_responses import make_sample_paper
    from zotero_arxiv_daily2markdown.executor import SingleDayArtifacts

    with open_dict(config):
        config.hugo.output_dir = str(tmp_path)

    executor = Executor.__new__(Executor)
    executor.config = config
    executor._correction_target_date = lambda: "2026-05-19"
    executor._build_single_day_artifacts = lambda corpus: SingleDayArtifacts(
        result=DailyRunResult(target_date="2026-05-19"),
        papers=[make_sample_paper(url="https://arxiv.org/abs/2605.00001v1")],
        overview_zh="overview zh",
        overview_en="overview en",
    )

    zh_path = tmp_path / "zh" / "posts" / "2026-05-19-arxiv-daily.md"
    en_path = tmp_path / "en" / "posts" / "2026-05-19-arxiv-daily.md"
    zh_path.parent.mkdir(parents=True, exist_ok=True)
    en_path.parent.mkdir(parents=True, exist_ok=True)
    zh_path.write_text(
        "- **Link**: [https://arxiv.org/abs/2605.00002v1](https://arxiv.org/abs/2605.00002v1)\n",
        encoding="utf-8",
    )
    en_path.write_text(
        "- **Link**: [https://arxiv.org/abs/2605.00002v1](https://arxiv.org/abs/2605.00002v1)\n",
        encoding="utf-8",
    )

    export_calls = []
    email_calls = []
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.export_to_hugo", lambda *args, **kwargs: export_calls.append(args))
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.send_email",
        lambda config, html, subject=None: email_calls.append((subject, html)),
    )

    executor._apply_previous_day_correction()

    assert len(export_calls) == 1
    assert export_calls[0][0][0].url == "https://arxiv.org/abs/2605.00001v1"
    assert email_calls and email_calls[0][0] == "arXiv Daily revision: 2026-05-19"
    assert "昨日修订：2026-05-19" in email_calls[0][1]


def test_previous_day_correction_ignores_revision_email_failure(config, monkeypatch, tmp_path):
    from omegaconf import open_dict
    from tests.canned_responses import make_sample_paper

    with open_dict(config):
        config.hugo.output_dir = str(tmp_path)

    executor = Executor.__new__(Executor)
    executor.config = config
    executor._correction_target_date = lambda: "2026-05-19"
    executor._build_single_day_artifacts = lambda corpus: SingleDayArtifacts(
        result=DailyRunResult(target_date="2026-05-19"),
        papers=[make_sample_paper(url="https://arxiv.org/abs/2605.00001v1")],
        overview_zh="overview zh",
        overview_en="overview en",
    )
    executor._send_error_email = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("revision email failure should not become run failure")
    )

    export_calls = []
    monkeypatch.setattr("zotero_arxiv_daily2markdown.executor.export_to_hugo", lambda *args, **kwargs: export_calls.append(args))
    monkeypatch.setattr(
        "zotero_arxiv_daily2markdown.executor.send_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("smtp timed out")),
    )

    executor._apply_previous_day_correction()

    assert len(export_calls) == 1


def test_run_date_range_skips_existing_hugo_outputs(config):
    from omegaconf import open_dict

    with open_dict(config):
        config.executor.skip_existing = True
        config.executor.historical_mode = "export_only"

    executor = Executor.__new__(Executor)
    executor.config = config
    executor._hugo_outputs_exist = lambda target_date: target_date == "2026-05-02"
    calls = []

    def run_single_day(corpus, *, send_email_enabled=True):
        calls.append(config.executor.target_date)
        return SimpleNamespace(target_date=config.executor.target_date, skipped=False, error=None)

    executor._run_single_day = run_single_day
    results = executor._run_date_range(["2026-05-01", "2026-05-02", "2026-05-03"], [])

    assert calls == ["2026-05-01", "2026-05-03"]
    assert [result.skipped for result in results] == [False, True, False]
