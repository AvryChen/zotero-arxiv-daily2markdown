"""Tests for BaseReranker: scoring, sorting, time decay, unknown reranker."""

import numpy as np
import pytest

from zotero_arxiv_daily2markdown.reranker.base import BaseReranker, get_reranker_cls
from tests.canned_responses import make_sample_paper, make_sample_corpus


class StubReranker(BaseReranker):
    """Reranker with a controlled similarity matrix for deterministic tests."""

    def __init__(self, sim_matrix: np.ndarray):
        self.config = None
        self._sim = sim_matrix

    def get_similarity_score(self, s1, s2):
        return self._sim


def test_rerank_scores_and_sorts():
    corpus = make_sample_corpus(3)
    papers = [make_sample_paper(title=f"Paper {i}") for i in range(2)]

    # Paper 1 has higher similarity to all corpus papers
    sim = np.array([
        [0.1, 0.1, 0.1],  # paper 0 — low
        [0.9, 0.9, 0.9],  # paper 1 — high
    ])
    reranker = StubReranker(sim)
    ranked = reranker.rerank(papers, corpus)
    assert ranked[0].title == "Paper 1"
    assert ranked[1].title == "Paper 0"
    assert ranked[0].score > ranked[1].score


def test_rerank_time_decay_weighting():
    corpus = make_sample_corpus(3)
    papers = [make_sample_paper(title="P")]

    # Only similar to the oldest paper (index 2 after reverse-sort by date)
    sim = np.array([[0.0, 0.0, 1.0]])
    reranker = StubReranker(sim)
    ranked_old = reranker.rerank(papers, corpus)
    score_old = ranked_old[0].score

    # Only similar to the newest paper (index 0 after reverse-sort by date)
    papers2 = [make_sample_paper(title="P")]
    sim2 = np.array([[1.0, 0.0, 0.0]])
    reranker2 = StubReranker(sim2)
    ranked_new = reranker2.rerank(papers2, corpus)
    score_new = ranked_new[0].score

    # Newest corpus paper gets higher time-decay weight, so score should be higher
    assert score_new > score_old


def test_rerank_single_candidate_single_corpus():
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper()]
    sim = np.array([[0.5]])
    reranker = StubReranker(sim)
    ranked = reranker.rerank(papers, corpus)
    assert len(ranked) == 1
    assert ranked[0].score is not None


def test_rerank_empty_corpus_assigns_zero_score_without_similarity_call():
    papers = [make_sample_paper(title="P")]

    class NoSimilarityReranker(StubReranker):
        def get_similarity_score(self, s1, s2):
            raise AssertionError("empty corpus should not call similarity")

    ranked = NoSimilarityReranker(np.empty((0, 0))).rerank(papers, [])

    assert ranked == papers
    assert ranked[0].score == 0.0


def test_rerank_empty_candidates_returns_empty_without_similarity_call():
    class NoSimilarityReranker(StubReranker):
        def get_similarity_score(self, s1, s2):
            raise AssertionError("empty candidates should not call similarity")

    assert NoSimilarityReranker(np.empty((0, 0))).rerank([], make_sample_corpus(1)) == []


def test_rerank_can_exclude_full_text():
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper(title="P", abstract="ABSTRACT", full_text="FULL TEXT")]
    captured = {}

    class CapturingReranker(StubReranker):
        def get_similarity_score(self, s1, s2):
            captured["candidate_texts"] = s1
            captured["corpus_texts"] = s2
            return np.array([[0.5]])

    reranker = CapturingReranker(np.array([[0.5]]))
    reranker.rerank(papers, corpus, include_full_text=False)

    assert captured["candidate_texts"] == ["P\n\nABSTRACT"]
    assert captured["corpus_texts"] == [corpus[0].ranking_text()]


def test_rerank_can_truncate_full_text():
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper(title="P", abstract="ABSTRACT", full_text="FULL TEXT THAT IS LONG")]
    captured = {}

    class CapturingReranker(StubReranker):
        def get_similarity_score(self, s1, s2):
            captured["candidate_texts"] = s1
            return np.array([[0.5]])

    reranker = CapturingReranker(np.array([[0.5]]))
    reranker.rerank(papers, corpus, include_full_text=True, max_full_text_chars=4)

    assert captured["candidate_texts"] == ["P\n\nABSTRACT\n\nFULL"]


def test_rerank_can_use_english_tldr():
    corpus = make_sample_corpus(1)
    papers = [make_sample_paper(title="P", abstract="ABSTRACT", tldr="中文总结", tldr_en="English TLDR")]
    captured = {}

    class CapturingReranker(StubReranker):
        def get_similarity_score(self, s1, s2):
            captured["candidate_texts"] = s1
            return np.array([[0.5]])

    reranker = CapturingReranker(np.array([[0.5]]))
    reranker.rerank(papers, corpus, include_full_text=False, include_english_tldr=True)

    assert captured["candidate_texts"] == ["P\n\nABSTRACT\n\nEnglish TLDR"]


def test_get_reranker_cls_unknown():
    with pytest.raises(ValueError, match="not found"):
        get_reranker_cls("nonexistent_reranker_xyz")
