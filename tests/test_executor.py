"""Tests for zotero_arxiv_daily2markdown.executor: normalize_path_patterns, filter_corpus, fetch_zotero_corpus, E2E."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily2markdown.executor import Executor, expand_date_range, normalize_path_patterns
from zotero_arxiv_daily2markdown.protocol import CorpusPaper


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


def test_run_shortlists_before_fetching_full_text_and_reranks(config, monkeypatch):
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
    assert enriched == ["Paper A", "Paper B", "Paper C"]
    assert sorted(tldr_calls) == ["Paper A", "Paper B", "Paper C"]
    assert sorted(tldr_en_calls) == ["Paper A", "Paper B", "Paper C"]
    assert rerank_calls[1][0] is False
    assert rerank_calls[1][1] is False
    assert rerank_calls[1][2] is True
    assert rerank_calls[1][3] is None
    assert rerank_calls[1][4] == ["Paper A", "Paper B", "Paper C"]
    assert rerank_calls[1][5] == ["FULL Paper A", "FULL Paper B", "FULL Paper C"]
    assert rerank_calls[1][6] == ["ZH Paper A", "ZH Paper B", "ZH Paper C"]
    assert rerank_calls[1][7] == ["EN Paper A", "EN Paper B", "EN Paper C"]
    assert affiliation_calls == ["Paper B", "Paper C"]


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
