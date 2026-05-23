from .base import BaseReranker, PreparedRerankCorpus, register_reranker
import logging
import warnings
import numpy as np


@register_reranker("local")
class LocalReranker(BaseReranker):
    def __init__(self, config):
        super().__init__(config)
        self._encoder = None

    def _get_encoder(self):
        from sentence_transformers import SentenceTransformer
        if not self.config.executor.debug:
            from transformers.utils import logging as transformers_logging
            from huggingface_hub.utils import logging as hf_logging

            transformers_logging.set_verbosity_error()
            hf_logging.set_verbosity_error()
            logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
            logging.getLogger("sentence_transformers.SentenceTransformer").setLevel(logging.ERROR)
            logging.getLogger("transformers").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
            logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
            warnings.filterwarnings("ignore", category=FutureWarning)

        if self._encoder is None:
            self._encoder = SentenceTransformer(self.config.reranker.local.model, trust_remote_code=True)
        return self._encoder

    def _encode(self, texts: list[str]) -> np.ndarray:
        encoder = self._get_encoder()
        if self.config.reranker.local.encode_kwargs:
            encode_kwargs = dict(self.config.reranker.local.encode_kwargs)
        else:
            encode_kwargs = {}
        encode_kwargs.setdefault("convert_to_numpy", True)
        features = encoder.encode(texts, **encode_kwargs, show_progress_bar=True)
        return np.asarray(features, dtype=np.float32)

    def prepare_corpus(self, corpus) -> PreparedRerankCorpus:
        prepared = super().prepare_corpus(corpus)
        if prepared.texts:
            prepared.embeddings = self._encode(prepared.texts)
        return prepared

    def get_similarity_score_to_prepared_corpus(
        self,
        s1: list[str],
        prepared_corpus: PreparedRerankCorpus,
    ) -> np.ndarray:
        s1_feature = self._encode(s1)
        return self._cosine_similarity(s1_feature, prepared_corpus.embeddings)

    def get_similarity_score(self, s1: list[str], s2: list[str]) -> np.ndarray:
        s1_feature = self._encode(s1)
        s2_feature = self._encode(s2)
        return self._cosine_similarity(s1_feature, s2_feature)

    @staticmethod
    def _cosine_similarity(s1_feature: np.ndarray, s2_feature: np.ndarray) -> np.ndarray:
        s1_norm = s1_feature / np.linalg.norm(s1_feature, axis=1, keepdims=True)
        s2_norm = s2_feature / np.linalg.norm(s2_feature, axis=1, keepdims=True)
        return np.dot(s1_norm, s2_norm.T)
