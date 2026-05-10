"""
Pydantic models for the AI newsletter generator.

ArticleItem      — raw fetched article (pre-analysis)
AnalyzedItem     — enriched article after Ollama analysis
ArticleAnalysis  — minimal schema for structured Ollama output (subset of AnalyzedItem)
Source           — a news source entry from sources.json
CurationResult   — Claude's weekly source-curation recommendations
"""

from typing import Literal

from pydantic import BaseModel, Field


class Source(BaseModel):
    type: Literal["rss", "arxiv", "twitter", "blog"]
    name: str
    url: str = ""
    query: str = ""        # for arxiv: e.g. "cat:cs.AI"
    handle: str = ""       # for twitter: e.g. "AnthropicAI"
    tier: int = 1          # 1=must-have, 2=good, 3=nice
    active: bool = True
    last_checked: str = ""


class ArticleItem(BaseModel):
    title: str
    url: str
    source_name: str
    source_type: str
    published: str         # ISO date string
    raw_text: str          # first 2000 chars
    author: str = ""


class ArticleAnalysis(BaseModel):
    """Minimal schema used for Ollama structured output — fields Ollama fills in."""
    summary: str = Field(description="~80 word plain-English summary of the article")
    relevance: int = Field(description="Relevance score 0-10 for AI professionals")
    tags: list[str] = Field(description="Up to 5 topic tags e.g. ['LLMs', 'safety', 'multimodal']")
    is_major: bool = Field(description="True for major announcements, False for incremental updates")


class AnalyzedItem(BaseModel):
    title: str
    url: str
    source_name: str
    source_type: str
    published: str
    author: str
    summary: str           # ~80 word Ollama summary
    relevance: int         # 0-10
    tags: list[str]        # ["LLMs", "safety", ...]
    is_major: bool         # major announcement vs incremental


class CurationResult(BaseModel):
    sources_to_add: list[Source] = Field(default_factory=list)
    source_names_to_deactivate: list[str] = Field(default_factory=list)
    notes: str = ""
