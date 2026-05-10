"""
Two independent LangGraph pipelines:

  RESEARCH PIPELINE  (run any time — fetches, analyzes, saves to Qdrant)
    fetch → analyze → ingest → END

  NEWSLETTER PIPELINE  (run once daily — reads from Qdrant, publishes)
    qdrant_fetch → select → write → publish → END

Run together:   python -m generate
Research only:  python -m generate --research
Newsletter only: python -m generate --newsletter

The research pipeline can run multiple times a day — content lands in Qdrant
immediately and is searchable. The newsletter pipeline pulls whatever is in
Qdrant (filtered to LOOKBACK_DAYS), so it benefits from every research run.
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .analyzer import analyze_all
from .config import LOOKBACK_DAYS, QDRANT_COLLECTION, TOP_N_ARTICLES
from .curator import refresh_sources, should_refresh
from .git_ops import git_commit_and_push
from .ingestor import collection_count, ingest_articles, setup_collection
from .llm import warmup_ollama
from .schemas import AnalyzedItem, ArticleItem
from .sources import fetch_all, load_sources
from .state import already_published, get_issue_number, load_state, mark_published, save_state
from .writer import write_newsletter_post


# ── State definitions ─────────────────────────────────────────────────────────

class ResearchState(TypedDict):
    app_state: dict
    raw_articles: list[dict]        # ArticleItem dicts from sources
    analyzed_articles: list[dict]   # AnalyzedItem dicts after Ollama
    ingested_count: int             # how many new articles landed in Qdrant
    date_str: str
    errors: list[str]


class NewsletterState(TypedDict):
    app_state: dict
    analyzed_articles: list[dict]   # pulled from Qdrant (already analyzed)
    selected_articles: list[dict]   # after select/editor node
    newsletter_content: str
    post_path: Path | None
    issue_number: int
    date_str: str
    errors: list[str]


# ── Research nodes ────────────────────────────────────────────────────────────

def fetch_node(state: ResearchState) -> dict:
    """
    Curate sources if overdue, then fetch all articles since LOOKBACK_DAYS.
    No already-published filter here — the ingestor handles dedup via upsert.
    """
    t0 = time.perf_counter()
    app_state = state["app_state"]
    errors: list[str] = list(state.get("errors", []))

    if should_refresh(app_state):
        try:
            refresh_sources(app_state)
        except Exception as e:
            msg = f"Source curation failed: {e}"
            print(f"  ! {msg}")
            errors.append(msg)

    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    print(f"  [fetch] Fetching articles since {since.date()}...")

    try:
        articles = fetch_all(since)
    except Exception as e:
        msg = f"fetch_all failed: {e}"
        print(f"  ! {msg}")
        errors.append(msg)
        articles = []

    print(f"  [fetch] {len(articles)} articles | {time.perf_counter() - t0:.1f}s")
    return {"raw_articles": [a.model_dump() for a in articles], "errors": errors}


def analyze_node(state: ResearchState) -> dict:
    """Analyze raw articles with Ollama structured output."""
    t0 = time.perf_counter()
    raw = state.get("raw_articles", [])
    if not raw:
        print("  [analyze] No articles to analyze.")
        return {"analyzed_articles": []}

    articles = [ArticleItem(**d) for d in raw]
    print(f"  [analyze] Analyzing {len(articles)} articles...")
    analyzed = analyze_all(articles)
    print(f"  [analyze] {len(analyzed)} done | {time.perf_counter() - t0:.1f}s")
    return {"analyzed_articles": [a.model_dump() for a in analyzed]}


def ingest_node(state: ResearchState) -> dict:
    """
    Embed analyzed articles and upsert to Qdrant.
    Upsert is idempotent — running twice on the same URL is safe.
    Content is immediately searchable after this node.
    """
    t0 = time.perf_counter()
    analyzed = [AnalyzedItem(**d) for d in state.get("analyzed_articles", [])]
    if not analyzed:
        print("  [ingest] Nothing to ingest.")
        return {"ingested_count": 0}

    setup_collection()
    count = ingest_articles(analyzed)
    total = collection_count()

    app_state = state["app_state"]
    app_state["last_research_run"] = datetime.now(timezone.utc).isoformat()
    save_state(app_state)

    print(f"  [ingest] {count} new · {total} total in Qdrant | {time.perf_counter() - t0:.1f}s")
    return {"ingested_count": count, "app_state": app_state}


# ── Newsletter nodes ──────────────────────────────────────────────────────────

def qdrant_fetch_node(state: NewsletterState) -> dict:
    """
    Pull today's analyzed articles from Qdrant instead of re-fetching live sources.
    Filters out URLs already published in a previous newsletter issue.
    """
    t0 = time.perf_counter()
    app_state = state["app_state"]
    errors: list[str] = list(state.get("errors", []))

    try:
        from qdrant_client.models import FieldCondition, Filter, Range
        from .ingestor import _client

        cutoff = int(
            (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp()
        )

        results, _ = _client().scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="published_timestamp", range=Range(gte=cutoff))]
            ),
            limit=500,
            with_payload=True,
            with_vectors=False,
        )

        articles = []
        for point in results:
            p = point.payload
            if not already_published(app_state, p["url"]):
                articles.append({
                    "title":       p["title"],
                    "url":         p["url"],
                    "source_name": p["source_name"],
                    "source_type": p["source_type"],
                    "published":   p["published"],
                    "author":      p.get("author", ""),
                    "summary":     p["summary"],
                    "relevance":   p["relevance"],
                    "tags":        p["tags"],
                    "is_major":    p["is_major"],
                })

        print(f"  [qdrant_fetch] {len(articles)} articles from last {LOOKBACK_DAYS}d | {time.perf_counter() - t0:.1f}s")
        return {"analyzed_articles": articles, "errors": errors}

    except Exception as e:
        msg = f"qdrant_fetch failed: {e}"
        print(f"  [qdrant_fetch] ! {msg}")
        errors.append(msg)
        return {"analyzed_articles": [], "errors": errors}


def select_node(state: NewsletterState) -> dict:
    """Sort by (is_major desc, relevance desc, tier asc) and pick TOP_N_ARTICLES."""
    t0 = time.perf_counter()
    analyzed = [AnalyzedItem(**d) for d in state.get("analyzed_articles", [])]

    sources = load_sources()
    tier_map = {s.name: s.tier for s in sources}

    worthy = [a for a in analyzed if a.relevance >= 4]
    print(f"  [select] {len(worthy)}/{len(analyzed)} pass relevance filter (>=4)")

    worthy.sort(key=lambda a: (
        0 if a.is_major else 1,
        -a.relevance,
        tier_map.get(a.source_name, 99),
    ))

    selected = worthy[:TOP_N_ARTICLES]
    print(f"  [select] {len(selected)} selected | {time.perf_counter() - t0:.1f}s")
    return {"selected_articles": [a.model_dump() for a in selected]}


def write_node(state: NewsletterState) -> dict:
    """Write the Jekyll newsletter post using Ollama for the intro paragraph."""
    t0 = time.perf_counter()
    selected = [AnalyzedItem(**d) for d in state.get("selected_articles", [])]
    issue_number = state["issue_number"]
    date_str = state["date_str"]
    errors: list[str] = list(state.get("errors", []))

    if not selected:
        msg = "No articles selected — skipping post write"
        print(f"  [write] ! {msg}")
        errors.append(msg)
        return {"post_path": None, "newsletter_content": "", "errors": errors}

    print(f"  [write] Writing issue #{issue_number} with {len(selected)} articles...")
    try:
        content, post_path = write_newsletter_post(selected, issue_number, date_str)
        print(f"  [write] {post_path.name} | {time.perf_counter() - t0:.1f}s")
        return {"newsletter_content": content, "post_path": post_path, "errors": errors}
    except Exception as e:
        msg = f"write_newsletter_post failed: {e}"
        print(f"  [write] ! {msg}")
        errors.append(msg)
        return {"post_path": None, "newsletter_content": "", "errors": errors}


def publish_node(state: NewsletterState) -> dict:
    """Git commit + push, mark URLs published, save state."""
    t0 = time.perf_counter()
    post_path = state.get("post_path")
    app_state = state["app_state"]
    issue_number = state["issue_number"]
    selected = [AnalyzedItem(**d) for d in state.get("selected_articles", [])]
    errors: list[str] = list(state.get("errors", []))

    if post_path is None:
        print("  [publish] No post — skipping git push")
    else:
        try:
            git_commit_and_push(post_path, issue_number)
        except Exception as e:
            msg = f"git_commit_and_push failed: {e}"
            print(f"  [publish] ! {msg}")
            errors.append(msg)

    mark_published(app_state, selected)
    app_state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(app_state)
    print(f"  [publish] State saved | {time.perf_counter() - t0:.1f}s")
    return {"app_state": app_state, "errors": errors}


# ── Build research graph ──────────────────────────────────────────────────────

_research_builder = StateGraph(ResearchState)
_research_builder.add_node("fetch",   fetch_node)
_research_builder.add_node("analyze", analyze_node)
_research_builder.add_node("ingest",  ingest_node)
_research_builder.set_entry_point("fetch")
_research_builder.add_edge("fetch",   "analyze")
_research_builder.add_edge("analyze", "ingest")
_research_builder.add_edge("ingest",  END)
research_pipeline = _research_builder.compile()


# ── Build newsletter graph ────────────────────────────────────────────────────

_newsletter_builder = StateGraph(NewsletterState)
_newsletter_builder.add_node("qdrant_fetch", qdrant_fetch_node)
_newsletter_builder.add_node("select",       select_node)
_newsletter_builder.add_node("write",        write_node)
_newsletter_builder.add_node("publish",      publish_node)
_newsletter_builder.set_entry_point("qdrant_fetch")
_newsletter_builder.add_edge("qdrant_fetch", "select")
_newsletter_builder.add_edge("select",       "write")
_newsletter_builder.add_edge("write",        "publish")
_newsletter_builder.add_edge("publish",      END)
newsletter_pipeline = _newsletter_builder.compile()


# ── Public entry points ───────────────────────────────────────────────────────

def _banner(title: str):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def run_research() -> int:
    """Fetch + analyze + ingest to Qdrant. Returns count of new articles."""
    _banner("Research Pipeline — fetch → analyze → ingest")
    warmup_ollama()
    app_state = load_state()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    initial: ResearchState = {
        "app_state":        app_state,
        "raw_articles":     [],
        "analyzed_articles": [],
        "ingested_count":   0,
        "date_str":         date_str,
        "errors":           [],
    }

    t0 = time.perf_counter()
    final = research_pipeline.invoke(initial)
    count = final.get("ingested_count", 0)
    errors = final.get("errors", [])

    print(f"\n{'=' * 60}")
    print(f"  Research done in {time.perf_counter() - t0:.1f}s — {count} new articles in Qdrant")
    if errors:
        for err in errors:
            print(f"    ! {err}")
    print(f"{'=' * 60}\n")
    return count


def run_newsletter() -> Path | None:
    """Read from Qdrant + select + write + publish. Returns post Path or None."""
    _banner("Newsletter Pipeline — qdrant_fetch → select → write → publish")
    app_state = load_state()
    issue_number = get_issue_number(app_state)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    initial: NewsletterState = {
        "app_state":          app_state,
        "analyzed_articles":  [],
        "selected_articles":  [],
        "newsletter_content": "",
        "post_path":          None,
        "issue_number":       issue_number,
        "date_str":           date_str,
        "errors":             [],
    }

    t0 = time.perf_counter()
    final = newsletter_pipeline.invoke(initial)
    post_path = final.get("post_path")
    errors = final.get("errors", [])

    print(f"\n{'=' * 60}")
    if post_path:
        print(f"  Issue #{issue_number} done in {time.perf_counter() - t0:.1f}s — {post_path.name}")
    else:
        print(f"  Done in {time.perf_counter() - t0:.1f}s — No post written")
    if errors:
        for err in errors:
            print(f"    ! {err}")
    print(f"{'=' * 60}\n")
    return post_path


def run_pipeline() -> Path | None:
    """Run both pipelines in sequence: research then newsletter."""
    run_research()
    return run_newsletter()
