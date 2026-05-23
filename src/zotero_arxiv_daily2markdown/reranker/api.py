from .base import BaseReranker, PreparedRerankCorpus, register_reranker
from openai import OpenAI
import numpy as np


@register_reranker("api")
class ApiReranker(BaseReranker):
    def __init__(self, config):
        super().__init__(config)
        self._client = OpenAI(api_key=self.config.reranker.api.key, base_url=self.config.reranker.api.base_url)

    def prepare_corpus(self, corpus) -> PreparedRerankCorpus:
        prepared = super().prepare_corpus(corpus)
        if prepared.texts:
            prepared.embeddings = self._embed_texts(prepared.texts)
        return prepared

    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        s1_embeddings = self._embed_texts(s1)
        s2_embeddings = self._embed_texts(s2)
        return self._cosine_similarity(s1_embeddings, s2_embeddings)

    def get_similarity_score_to_prepared_corpus(
        self,
        s1: list[str],
        prepared_corpus: PreparedRerankCorpus,
    ) -> np.ndarray:
        s1_embeddings = self._embed_texts(s1)
        return self._cosine_similarity(s1_embeddings, prepared_corpus.embeddings)

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        batch_size = self.config.reranker.api.get("batch_size") or 64
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self._client.embeddings.create(
                input=batch,
                model=self.config.reranker.api.model
            )
            all_embeddings.extend([r.embedding for r in response.data])
        return np.array(all_embeddings)

    @staticmethod
    def _cosine_similarity(s1_embeddings: np.ndarray, s2_embeddings: np.ndarray) -> np.ndarray:
        s1_embeddings_normalized = s1_embeddings / np.linalg.norm(s1_embeddings, axis=1, keepdims=True)
        s2_embeddings_normalized = s2_embeddings / np.linalg.norm(s2_embeddings, axis=1, keepdims=True)
        return np.dot(s1_embeddings_normalized, s2_embeddings_normalized.T) # [n_s1, n_s2]
