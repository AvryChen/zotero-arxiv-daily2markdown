from loguru import logger
from pyzotero import zotero
from omegaconf import DictConfig, ListConfig, OmegaConf
from .utils import glob_match, to_bool
from .retriever import get_retriever_cls
from .protocol import CorpusPaper
import random
from datetime import datetime
from .reranker import get_reranker_cls
from .construct_email import render_email
from .utils import send_email
from .hugo_exporter import export_to_hugo
from openai import OpenAI
from tqdm import tqdm


def normalize_path_patterns(patterns: list[str] | ListConfig | None, config_key: str) -> list[str] | None:
    if patterns is None:
        return None

    if not isinstance(patterns, (list, ListConfig)):
        raise TypeError(
            f"config.zotero.{config_key} must be a list of glob patterns or null, "
            'for example ["2026/survey/**"]. Single strings are not supported.'
        )

    if any(not isinstance(pattern, str) for pattern in patterns):
        raise TypeError(f"config.zotero.{config_key} must contain only glob pattern strings.")

    return list(patterns)


class Executor:
    def __init__(self, config:DictConfig):
        self.config = config
        self.include_path_patterns = normalize_path_patterns(config.zotero.include_path, "include_path")
        self.ignore_path_patterns = normalize_path_patterns(config.zotero.ignore_path, "ignore_path")
        
        # Normalize debug flag in config to handle string "false" from .env
        OmegaConf.set_struct(config, False)
        config.executor.debug = to_bool(config.executor.get("debug", False))
        self.debug = config.executor.debug

        self.retrievers = {
            source: get_retriever_cls(source)(config) for source in config.executor.source
        }
        self.reranker = get_reranker_cls(config.executor.reranker)(config)
        self.openai_client = OpenAI(api_key=config.llm.api.key, base_url=config.llm.api.base_url)

    def fetch_zotero_corpus(self) -> list[CorpusPaper]:
        logger.info("Fetching zotero corpus")
        zot = zotero.Zotero(self.config.zotero.user_id, 'user', self.config.zotero.api_key)
        collections = zot.everything(zot.collections())
        collections = {c['key']:c for c in collections}
        corpus = zot.everything(zot.items(itemType='conferencePaper || journalArticle || preprint'))
        corpus = [c for c in corpus if c['data']['abstractNote'] != '']
        def get_collection_path(col_key:str) -> str:
            if p := collections[col_key]['data']['parentCollection']:
                return get_collection_path(p) + '/' + collections[col_key]['data']['name']
            else:
                return collections[col_key]['data']['name']
        for c in corpus:
            paths = [get_collection_path(col) for col in c['data']['collections']]
            c['paths'] = paths
        logger.info(f"Fetched {len(corpus)} zotero papers")
        return [CorpusPaper(
            title=c['data']['title'],
            abstract=c['data']['abstractNote'],
            added_date=datetime.strptime(c['data']['dateAdded'], '%Y-%m-%dT%H:%M:%SZ'),
            paths=c['paths']
        ) for c in corpus]
    
    def filter_corpus(self, corpus:list[CorpusPaper]) -> list[CorpusPaper]:
        if self.include_path_patterns:
            logger.info(f"Selecting zotero papers matching include_path: {self.include_path_patterns}")
            corpus = [
                c for c in corpus
                if any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.include_path_patterns
                )
            ]
        if self.ignore_path_patterns:
            logger.info(f"Excluding zotero papers matching ignore_path: {self.ignore_path_patterns}")
            corpus = [
                c for c in corpus
                if not any(
                    glob_match(path, pattern)
                    for path in c.paths
                    for pattern in self.ignore_path_patterns
                )
            ]
        if self.include_path_patterns or self.ignore_path_patterns:
            samples = random.sample(corpus, min(5, len(corpus)))
            samples = '\n'.join([c.title + ' - ' + '\n'.join(c.paths) for c in samples])
            logger.info(f"Selected {len(corpus)} zotero papers:\n{samples}\n...")
        return corpus

    
    def run(self):
        corpus = self.fetch_zotero_corpus()
        corpus = self.filter_corpus(corpus)
        if len(corpus) == 0:
            logger.error(f"No zotero papers found. Please check your zotero settings:\n{self.config.zotero}")
            return
        all_papers = []
        for source, retriever in self.retrievers.items():
            logger.info(f"Retrieving {source} papers...")
            papers = retriever.retrieve_papers()
            if len(papers) == 0:
                logger.info(f"No {source} papers found")
                continue
            logger.info(f"Retrieved {len(papers)} {source} papers")
            all_papers.extend(papers)
        logger.info(f"Total {len(all_papers)} papers retrieved from all sources")
        reranked_papers = []
        if len(all_papers) > 0:
            logger.info("Reranking papers...")
            reranked_papers = self.reranker.rerank(all_papers, corpus)
            
            # Filter by score threshold
            threshold = self.config.executor.get("score_threshold", 3.0)
            reranked_papers = [p for p in reranked_papers if p.score is not None and p.score >= threshold]
            
            # Limit to max_paper_num
            reranked_papers = reranked_papers[:self.config.executor.max_paper_num]
            
            if len(reranked_papers) == 0:
                logger.info(f"No papers met the score threshold of {threshold}.")
                if not self.config.executor.send_empty:
                    return
            else:
                logger.info(f"Selected {len(reranked_papers)} papers above threshold {threshold}")

            logger.info("Generating TLDR and affiliations...")
            for p in tqdm(reranked_papers):
                p.generate_tldr(self.openai_client, self.config.llm)
                p.generate_english_tldr(self.openai_client, self.config.llm)
                p.generate_affiliations(self.openai_client, self.config.llm)
        elif not self.config.executor.send_empty:
            logger.info("No new papers found. No email will be sent.")
            return
            
        logger.info("Generating daily overview...")
        overview_zh = ""
        overview_en = ""
        high_score_papers = [p for p in reranked_papers if p.score is not None and p.score >= 3.0]
        if high_score_papers:
            papers_info = []
            for i, p in enumerate(high_score_papers, 1):
                affil = ', '.join(p.affiliations) if p.affiliations else 'Unknown'
                papers_info.append(f"[{i}] Title: {p.title}\nAuthors: {', '.join(p.authors)}\nAffiliations: {affil}\nSummary: {p.tldr}")
            papers_text = "\n\n".join(papers_info)
            
            prompt_cfg = self.config.get("prompt", {})
            topic = prompt_cfg.get("topic", "research")
            role = prompt_cfg.get("role", "专业的学术编辑")
            overview_template = prompt_cfg.get("overview_zh", "请总结以下论文: {topic}")
            translation_prompt = prompt_cfg.get("translation_en", "Please translate:")

            prompt_zh = overview_template.format(topic=topic) + f"\n\n{papers_text}"
            
            try:
                response = self.openai_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": role},
                        {"role": "user", "content": prompt_zh}
                    ],
                    **self.config.llm.get('generation_kwargs', {})
                )
                overview_zh = response.choices[0].message.content
                
                prompt_en = f"{translation_prompt}\n\n{overview_zh}"
                response_en = self.openai_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": "You are a professional translator for academic papers."},
                        {"role": "user", "content": prompt_en}
                    ],
                    **self.config.llm.get('generation_kwargs', {})
                )
                overview_en = response_en.choices[0].message.content
            except Exception as e:
                logger.error(f"Failed to generate overview: {e}")

        logger.info("Sending email...")
        email_content = render_email(reranked_papers)
        send_email(self.config, email_content)
        logger.info("Email sent successfully")
        export_to_hugo(reranked_papers, self.config, overview_zh, overview_en)
