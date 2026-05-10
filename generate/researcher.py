"""
Deep research agent — enriches high-relevance articles with web search + Ollama synthesis.

Search backend priority:
  1. Tavily     — best quality, built for AI agents (needs TAVILY_API_KEY)
  2. Perplexica — self-hosted on Minisforum (needs PERPLEXICA_URL, Docker setup)
  3. DuckDuckGo — always available, no key needed (rate-limited fallback)

For each article above RESEARCH_THRESHOLD:
  1. Ollama generates 2 focused search queries
  2. Searches run in parallel across all queries
  3. Results are deduplicated by URL
  4. Ollama synthesizes findings into research_notes (~150 words)
  5. research_notes + research_sources are stored in Qdrant alongside the article
"""

from __future__ import annotations

import concurrent.futures
import time

from .config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    PERPLEXICA_URL,
    RESEARCH_MAX_ARTICLES,
    RESEARCH_THRESHOLD,
)
from .schemas import AnalyzedItem

import ollama as _ollama_lib

_ollama = _ollama_lib.Client(host=OLLAMA_HOST)


# ── Search backends ───────────────────────────────────────────────────────────

def _search_perplexica(query: str, max_results: int = 5) -> list[dict]:
    import httpx
    resp = httpx.post(
        f"{PERPLEXICA_URL}/api/search",
        json={"query": query, "focusMode": "academicSearch", "optimizationMode": "balanced"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    sources = data.get("sources", [])[:max_results]
    return [
        {
            "title":   s.get("metadata", {}).get("title", ""),
            "url":     s.get("metadata", {}).get("url", ""),
            "snippet": s.get("pageContent", "")[:400],
        }
        for s in sources
    ]


def _search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")[:400]}
        for r in results
    ]


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Try Perplexica → DuckDuckGo, return first successful result."""
    if PERPLEXICA_URL:
        try:
            return _search_perplexica(query, max_results)
        except Exception as e:
            print(f"  [research] Perplexica failed ({e}), falling back to DuckDuckGo...")

    try:
        return _search_duckduckgo(query, max_results)
    except Exception as e:
        print(f"  [research] DuckDuckGo failed: {e}")
        return []


# ── Ollama helpers ────────────────────────────────────────────────────────────

def _generate_search_queries(item: AnalyzedItem) -> list[str]:
    """Ask Ollama for 2 focused search queries to research this article further."""
    prompt = (
        f"You are a research assistant helping investigate an AI news article.\n\n"
        f"Article: {item.title}\n"
        f"Summary: {item.summary}\n"
        f"Tags: {', '.join(item.tags)}\n\n"
        f"Generate exactly 2 focused web search queries to find:\n"
        f"1. Related research papers or technical details\n"
        f"2. Reactions, context, or broader implications\n\n"
        f"Output only the 2 queries, one per line, no numbering or extra text."
    )
    resp = _ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"num_predict": 80, "temperature": 0.3},
    )
    lines = [l.strip() for l in resp.message.content.strip().splitlines() if l.strip()]
    return lines[:2]


def _synthesize(item: AnalyzedItem, results: list[dict]) -> str:
    """Ollama synthesizes search results into a research paragraph."""
    if not results:
        return ""

    snippets = "\n\n".join(
        f"[{i+1}] {r['title']}\n{r['snippet']}"
        for i, r in enumerate(results[:8])
    )
    prompt = (
        f"You are a research analyst. An AI professional is reading about:\n"
        f"'{item.title}'\n\n"
        f"Here are related web search findings:\n{snippets}\n\n"
        f"Write a tight 120-150 word research note that:\n"
        f"- Adds context NOT already in the article summary\n"
        f"- Highlights the most important related work or reaction\n"
        f"- Stays factual — only what the sources support\n"
        f"- Ends with one sentence on why this matters for the field\n\n"
        f"Write only the research note, no preamble."
    )
    resp = _ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"num_predict": 256, "temperature": 0.2},
    )
    return resp.message.content.strip()


# ── Main entry point ──────────────────────────────────────────────────────────

def research_article(item: AnalyzedItem) -> dict:
    """
    Run deep research on one article.
    Returns dict with research_notes and research_sources to merge into the article.
    """
    t0 = time.perf_counter()

    queries = _generate_search_queries(item)
    if not queries:
        return {}

    # Run all queries in parallel
    all_results: list[dict] = []
    seen_urls: set[str] = {item.url}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as ex:
        futures = [ex.submit(web_search, q) for q in queries]
        for f in concurrent.futures.as_completed(futures):
            for r in f.result():
                if r.get("url") and r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    all_results.append(r)

    notes = _synthesize(item, all_results)
    elapsed = time.perf_counter() - t0
    print(f"  [research] '{item.title[:50]}' — {len(all_results)} sources | {elapsed:.1f}s")

    return {
        "research_notes":   notes,
        "research_sources": all_results[:8],
    }


def research_all(items: list[AnalyzedItem]) -> dict[str, dict]:
    """
    Research all items in parallel (max 3 concurrent to avoid rate limits).
    Returns a map of url → {research_notes, research_sources}.
    """
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(research_article, item): item.url for item in items}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as e:
                print(f"  [research] ! Failed for {url[:60]}: {e}")
    return results
