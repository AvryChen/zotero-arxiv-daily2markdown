from abc import ABC, abstractmethod
from dataclasses import dataclass
from omegaconf import DictConfig
from ..protocol import Paper, CorpusPaper
import numpy as np
from typing import Any
from typing import Type


@dataclass
class PreparedRerankCorpus:
    texts: list[str]
    time_decay_weight: np.ndarray
    embeddings: Any = None


class BaseReranker(ABC):
    supports_prepared_corpus = True

    def __init__(self, config:DictConfig):
        self.config = config

    def prepare_corpus(self, corpus: list[CorpusPaper]) -> PreparedRerankCorpus:
        corpus = sorted(corpus, key=lambda x: x.added_date, reverse=True)
        if len(corpus) == 0:
            return PreparedRerankCorpus(texts=[], time_decay_weight=np.array([]))

        time_decay_weight = 1 / (1 + np.log10(np.arange(len(corpus)) + 1))
        time_decay_weight = time_decay_weight / time_decay_weight.sum()
        return PreparedRerankCorpus(
            texts=[c.ranking_text() for c in corpus],
            time_decay_weight=time_decay_weight,
        )

    def rerank(
        self,
        candidates:list[Paper],
        corpus:list[CorpusPaper],
        *,
        include_full_text: bool = True,
        include_tldr: bool = False,
        include_english_tldr: bool = False,
        max_full_text_chars: int | None = None,
        prepared_corpus: PreparedRerankCorpus | None = None,
    ) -> list[Paper]:
        if len(candidates) == 0:
            return candidates

        prepared_corpus = prepared_corpus or self.prepare_corpus(corpus)
        if len(prepared_corpus.texts) == 0:
            for candidate in candidates:
                candidate.score = 0.0
            return candidates

        sim = self.get_similarity_score_to_prepared_corpus(
            [
                c.ranking_text(
                    include_full_text=include_full_text,
                    include_tldr=include_tldr,
                    include_english_tldr=include_english_tldr,
                    max_full_text_chars=max_full_text_chars,
                )
                for c in candidates
            ],
            prepared_corpus,
        )
        assert sim.shape == (len(candidates), len(prepared_corpus.texts))
        scores = (sim * prepared_corpus.time_decay_weight).sum(axis=1) * 10 # [n_candidate]
        for s,c in zip(scores,candidates):
            c.score = s
        candidates = sorted(candidates,key=lambda x: x.score,reverse=True)
        return candidates

    def get_similarity_score_to_prepared_corpus(
        self,
        s1: list[str],
        prepared_corpus: PreparedRerankCorpus,
    ) -> np.ndarray:
        return self.get_similarity_score(s1, prepared_corpus.texts)
    
    @abstractmethod
    def get_similarity_score(self, s1:list[str], s2:list[str]) -> np.ndarray:
        raise NotImplementedError

registered_rerankers = {}

def register_reranker(name:str):
    def decorator(cls):
        registered_rerankers[name] = cls
        return cls
    return decorator

def get_reranker_cls(name:str) -> Type[BaseReranker]:
    if name not in registered_rerankers:
        raise ValueError(f"Reranker {name} not found")
    return registered_rerankers[name]
