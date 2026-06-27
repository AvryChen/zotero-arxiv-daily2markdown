from dataclasses import dataclass, field
from typing import Optional, TypeVar
from datetime import datetime
import re
import tiktoken
from openai import OpenAI
from loguru import logger
import json
RawPaperItem = TypeVar('RawPaperItem')

@dataclass
class DomainDecision:
    paper_id: str
    is_in_domain: bool
    confidence: float = 0.0
    decision: str = "uncertain"
    reason: str = ""
    matched_concepts: list[str] = field(default_factory=list)
    negative_evidence: list[str] = field(default_factory=list)
    accepted: bool = False

    def to_dict(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "is_in_domain": self.is_in_domain,
            "confidence": self.confidence,
            "decision": self.decision,
            "reason": self.reason,
            "matched_concepts": self.matched_concepts,
            "negative_evidence": self.negative_evidence,
            "accepted": self.accepted,
        }

@dataclass
class Paper:
    source: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: Optional[str] = None
    full_text: Optional[str] = None
    tldr: Optional[str] = None
    tldr_en: Optional[str] = None
    tldr_zh_hant: Optional[str] = None
    affiliations: Optional[list[str]] = None
    score: Optional[float] = None
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    arxiv_id: Optional[str] = None
    doi: Optional[str] = None
    categories: list[str] = field(default_factory=list)
    primary_category: Optional[str] = None
    domain_decision: Optional[DomainDecision] = None
    full_text_path: Optional[str] = None
    pdf_path: Optional[str] = None
    text_sha256: Optional[str] = None
    pdf_sha256: Optional[str] = None
    full_text_source: Optional[str] = None
    full_text_errors: dict[str, str] = field(default_factory=dict)
    pdf_bytes: Optional[bytes] = field(default=None, repr=False)

    def ranking_text(
        self,
        include_full_text: bool = True,
        include_tldr: bool = False,
        include_english_tldr: bool = False,
        max_full_text_chars: int | None = None,
    ) -> str:
        parts = [self.title.strip(), self.abstract.strip()]
        if include_tldr and self.tldr:
            parts.append(self.tldr.strip())
        if include_english_tldr and self.tldr_en:
            parts.append(self.tldr_en.strip())
        if include_full_text and self.full_text:
            full_text = self.full_text.strip()
            if max_full_text_chars is not None and max_full_text_chars > 0:
                full_text = full_text[:max_full_text_chars]
            parts.append(full_text)
        return "\n\n".join(part for part in parts if part)

    def _generate_tldr_with_llm(self, openai_client:OpenAI,llm_params:dict) -> str:
        lang = llm_params.get('language', 'English')
        prompt = (
            f"Given the following information about a scientific paper, write a concise summary in {lang}.\n\n"
            "Requirements:\n"
            "- Output exactly one paragraph of plain body text.\n"
            "- Do not include a title, label, bullet list, numbering, or prefixes such as TLDR, Summary, 总结, or 摘要.\n"
            "- Focus on the paper's method, core findings, and conclusion.\n"
            "- Stay faithful to the source material and do not add information not supported by the paper.\n"
            "- Keep the summary roughly 200-400 Chinese characters when writing Chinese, or a comparable one-paragraph length in other languages.\n"
        )
        if self.title:
            prompt += f"Title:\n {self.title}\n\n"

        if self.abstract:
            prompt += f"Abstract: {self.abstract}\n\n"

        if self.full_text:
            prompt += f"Preview of main content:\n {self.full_text}\n\n"

        if not self.full_text and not self.abstract:
            logger.warning(f"Neither full text nor abstract is provided for {self.url}")
            return "Failed to generate TLDR. Neither full text nor abstract is provided"
        
        # use gpt-4o tokenizer for estimation
        enc = tiktoken.encoding_for_model("gpt-4o")
        prompt_tokens = enc.encode(prompt)
        prompt_tokens = prompt_tokens[:4000]  # truncate to 4000 tokens
        prompt = enc.decode(prompt_tokens)
        
        response = openai_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are an assistant who summarizes scientific papers accurately in {lang}. "
                        "Return exactly one paragraph of summary text with no heading, label, bullets, or extra commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            **llm_params.get('generation_kwargs', {})
        )
        tldr = response.choices[0].message.content
        tldr = re.sub(r"^(?:\*\*)?(?:TLDR|Summary|总结|摘要)(?:\*\*)?\s*[:：]\s*", "", tldr.strip(), flags=re.IGNORECASE)
        tldr = re.sub(r"\s+", " ", tldr).strip()
        return tldr
    
    def generate_tldr(self, openai_client:OpenAI,llm_params:dict) -> str:
        if self.tldr:
            return self.tldr
        try:
            tldr = self._generate_tldr_with_llm(openai_client,llm_params)
            self.tldr = tldr
            return tldr
        except Exception as e:
            logger.warning(f"Failed to generate tldr of {self.url}: {e}")
            tldr = self.abstract
            self.tldr = tldr
            return tldr

    def generate_english_tldr(self, openai_client: OpenAI, llm_params: dict) -> str:
        if self.tldr_en:
            return self.tldr_en
        if not self.tldr:
            return "No summary available to translate."
        try:
            prompt = (
                "Please translate the following Chinese summary of an academic paper into professional English.\n"
                "Return exactly one paragraph of body text only, with no title, label, bullets, or extra commentary.\n\n"
                f"{self.tldr}"
            )
            response = openai_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional translator for academic papers. Return exactly one paragraph of translated body text without labels or additional comments.",
                    },
                    {"role": "user", "content": prompt},
                ],
                **llm_params.get('generation_kwargs', {})
            )
            self.tldr_en = re.sub(
                r"^(?:\*\*)?(?:TLDR|Summary)(?:\*\*)?\s*[:：]\s*",
                "",
                response.choices[0].message.content.strip(),
                flags=re.IGNORECASE,
            )
            self.tldr_en = re.sub(r"\s+", " ", self.tldr_en).strip()
            return self.tldr_en
        except Exception as e:
            logger.warning(f"Failed to translate tldr of {self.url}: {e}")
            self.tldr_en = self.abstract
            return self.tldr_en

    def generate_traditional_chinese_tldr(self, openai_client: OpenAI, llm_params: dict) -> str:
        if self.tldr_zh_hant:
            return self.tldr_zh_hant
        if not self.tldr:
            return "暫無摘要。"
        try:
            prompt = (
                "請將下面這段學術論文中文摘要翻譯成繁體中文。\n"
                "請使用自然、專業的繁體中文，保留必要英文術語。\n"
                "只輸出一段正文，不要標題、標籤、條列或額外說明。\n\n"
                f"{self.tldr}"
            )
            response = openai_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "你是學術論文翻譯助手。請只輸出繁體中文正文，不要加任何說明。",
                    },
                    {"role": "user", "content": prompt},
                ],
                **llm_params.get('generation_kwargs', {})
            )
            self.tldr_zh_hant = re.sub(
                r"^(?:\*\*)?(?:TLDR|Summary|總結|摘要)(?:\*\*)?\s*[:：]\s*",
                "",
                response.choices[0].message.content.strip(),
                flags=re.IGNORECASE,
            )
            self.tldr_zh_hant = re.sub(r"\s+", " ", self.tldr_zh_hant).strip()
            return self.tldr_zh_hant
        except Exception as e:
            logger.warning(f"Failed to translate traditional Chinese tldr of {self.url}: {e}")
            self.tldr_zh_hant = self.tldr
            return self.tldr_zh_hant

    def _generate_affiliations_with_llm(self, openai_client:OpenAI,llm_params:dict) -> Optional[list[str]]:
        if self.full_text is not None:
            prompt = f"Given the beginning of a paper, extract the affiliations of the authors in a python list format, which is sorted by the author order. If there is no affiliation found, return an empty list '[]':\n\n{self.full_text}"
            # use gpt-4o tokenizer for estimation
            enc = tiktoken.encoding_for_model("gpt-4o")
            prompt_tokens = enc.encode(prompt)
            prompt_tokens = prompt_tokens[:2000]  # truncate to 2000 tokens
            prompt = enc.decode(prompt_tokens)
            affiliations = openai_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "You are an assistant who perfectly extracts affiliations of authors from a paper. You should return a python list of affiliations sorted by the author order, like [\"TsingHua University\",\"Peking University\"]. If an affiliation is consisted of multi-level affiliations, like 'Department of Computer Science, TsingHua University', you should return the top-level affiliation 'TsingHua University' only. Do not contain duplicated affiliations. If there is no affiliation found, you should return an empty list [ ]. You should only return the final list of affiliations, and do not return any intermediate results.",
                    },
                    {"role": "user", "content": prompt},
                ],
                **llm_params.get('generation_kwargs', {})
            )
            affiliations = affiliations.choices[0].message.content

            affiliations = re.search(r'\[.*?\]', affiliations, flags=re.DOTALL).group(0)
            affiliations = json.loads(affiliations)
            affiliations = list(set(affiliations))
            affiliations = [str(a) for a in affiliations]

            return affiliations
    
    def generate_affiliations(self, openai_client:OpenAI,llm_params:dict) -> Optional[list[str]]:
        if self.affiliations is not None:
            return self.affiliations
        try:
            affiliations = self._generate_affiliations_with_llm(openai_client,llm_params)
            self.affiliations = affiliations
            return affiliations
        except Exception as e:
            logger.warning(f"Failed to generate affiliations of {self.url}: {e}")
            self.affiliations = None
            return None
@dataclass
class CorpusPaper:
    title: str
    abstract: str
    added_date: datetime
    paths: list[str]

    def ranking_text(self) -> str:
        return "\n\n".join(part for part in (self.title.strip(), self.abstract.strip()) if part)
