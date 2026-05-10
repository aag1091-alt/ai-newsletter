"""
Article analysis using Ollama structured output.

Each article is sent to the local Ollama model which returns a structured
ArticleAnalysis (summary, relevance, tags, is_major). That result is then
merged with the original ArticleItem fields to produce a full AnalyzedItem.

analyze_all() runs analyses in parallel with a bounded thread pool.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .llm import _call_ollama_structured
from .schemas import AnalyzedItem, ArticleAnalysis, ArticleItem


def analyze_article(article: ArticleItem) -> AnalyzedItem | None:
    """
    Use Ollama structured output to analyze one article.
    Returns None if the model fails or the response is unusable.
    """
    prompt = (
        f"You are an AI research analyst. Analyze this article and return a structured assessment.\n\n"
        f"TITLE: {article.title}\n\n"
        f"CONTENT:\n{article.raw_text[:1500]}\n\n"
        f"Return JSON with:\n"
        f"  summary: ~80 word plain-English summary of what this article is about\n"
        f"  relevance: integer 0-10 for how relevant/important this is to AI professionals "
        f"(10=groundbreaking, 7+=notable, 4-6=informative, <4=low value)\n"
        f"  tags: list of up to 5 short topic tags (e.g. ['LLMs', 'safety', 'multimodal', 'agents'])\n"
        f"  is_major: true if this is a major announcement/breakthrough, false if incremental"
    )

    messages = [{"role": "user", "content": prompt}]
    result = _call_ollama_structured(messages, ArticleAnalysis)

    if result is None:
        return None

    return AnalyzedItem(
        title=article.title,
        url=article.url,
        source_name=article.source_name,
        source_type=article.source_type,
        published=article.published,
        author=article.author,
        summary=result.summary,
        relevance=result.relevance,
        tags=result.tags,
        is_major=result.is_major,
    )


def analyze_all(articles: list[ArticleItem]) -> list[AnalyzedItem]:
    """
    Analyze all articles in parallel using up to 4 Ollama workers.
    Failed analyses are skipped (logged but not raised).
    """
    if not articles:
        return []

    print(f"  Analyzing {len(articles)} articles with Ollama...")
    analyzed: list[AnalyzedItem] = []
    failed = 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(analyze_article, art): art for art in articles}
        for future in as_completed(futures):
            art = futures[future]
            try:
                result = future.result()
                if result is not None:
                    analyzed.append(result)
                else:
                    failed += 1
                    print(f"    ! Analysis returned None for: {art.title[:60]}")
            except Exception as e:
                failed += 1
                print(f"    ! Analysis error for '{art.title[:60]}': {e}")

    print(f"  Analysis complete: {len(analyzed)} ok, {failed} failed")
    return analyzed
