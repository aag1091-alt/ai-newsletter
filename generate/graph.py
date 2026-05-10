"""
LangGraph pipeline for the AI newsletter generator.

Linear pipeline (no conditional edges needed — fetch always proceeds):

  fetch → analyze → select → write → publish → END

Each node receives the full NewsletterState TypedDict and returns only the
keys it modified. LangGraph merges updates automatically.
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .analyzer import analyze_all
from .config import LOOKBACK_DAYS, TOP_N_ARTICLES
from .curator import refresh_sources, should_refresh
from .git_ops import git_commit_and_push
from .llm import warmup_ollama
from .schemas import AnalyzedItem, ArticleItem
from .sources import fetch_all, load_sources
from .state import (
    already_published,
    get_issue_number,
    load_state,
    mark_published,
    save_state,
)
from .writer import write_newsletter_post


# ── Graph state ───────────────────────────────────────────────────────────────

class NewsletterState(TypedDict):
    app_state: dict                   # loaded newsletter_state.json
    raw_articles: list[dict]          # ArticleItem dicts
    analyzed_articles: list[dict]     # AnalyzedItem dicts
    selected_articles: list[dict]     # top N AnalyzedItem dicts
    newsletter_content: str
    post_path: Path | None
    issue_number: int
    date_str: str
    errors: list[str]


# ── Node: fetch ────────────────────────────────────────────────────────────────

def fetch_node(state: NewsletterState) -> dict:
    """
    1. Run source curation if overdue (Claude CLI, weekly).
    2. Fetch all articles from active sources since LOOKBACK_DAYS ago.
    3. Filter out already-published URLs.
    """
    t0 = time.perf_counter()
    app_state = state["app_state"]
    errors: list[str] = list(state.get("errors", []))

    # Weekly source curation
    if should_refresh(app_state):
        try:
            refresh_sources(app_state)
        except Exception as e:
            msg = f"Source curation failed: {e}"
            print(f"  ! {msg}")
            errors.append(msg)

    # Determine lookback window
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    print(f"  [fetch] Fetching articles since {since.date()}...")

    try:
        articles = fetch_all(since)
    except Exception as e:
        msg = f"fetch_all failed: {e}"
        print(f"  ! {msg}")
        errors.append(msg)
        articles = []

    # Filter already-published URLs
    fresh = [a for a in articles if not already_published(app_state, a.url)]
    print(f"  [fetch] {len(fresh)} new articles (filtered {len(articles) - len(fresh)} already-published) | {time.perf_counter() - t0:.1f}s")

    return {
        "raw_articles": [a.model_dump() for a in fresh],
        "errors": errors,
    }


# ── Node: analyze ──────────────────────────────────────────────────────────────

def analyze_node(state: NewsletterState) -> dict:
    """Analyze all raw articles with Ollama structured output."""
    t0 = time.perf_counter()
    raw = state.get("raw_articles", [])
    if not raw:
        print("  [analyze] No articles to analyze.")
        return {"analyzed_articles": []}

    articles = [ArticleItem(**d) for d in raw]
    print(f"  [analyze] Analyzing {len(articles)} articles...")
    analyzed = analyze_all(articles)
    print(f"  [analyze] Done: {len(analyzed)} analyzed | {time.perf_counter() - t0:.1f}s")
    return {"analyzed_articles": [a.model_dump() for a in analyzed]}


# ── Node: select ───────────────────────────────────────────────────────────────

def select_node(state: NewsletterState) -> dict:
    """
    Sort by (is_major desc, relevance desc) and pick TOP_N_ARTICLES.
    Filters out items with relevance < 4.
    """
    t0 = time.perf_counter()
    analyzed = [AnalyzedItem(**d) for d in state.get("analyzed_articles", [])]

    # Join source tier from sources.json for tie-breaking
    sources = load_sources()
    tier_map = {s.name: s.tier for s in sources}

    # Filter low-relevance
    worthy = [a for a in analyzed if a.relevance >= 4]
    print(f"  [select] {len(worthy)}/{len(analyzed)} articles pass relevance filter (>=4)")

    # Sort: major first, then by relevance desc, then tier asc (lower=better)
    worthy.sort(
        key=lambda a: (
            0 if a.is_major else 1,
            -a.relevance,
            tier_map.get(a.source_name, 99),
        )
    )

    selected = worthy[:TOP_N_ARTICLES]
    print(f"  [select] Selected {len(selected)} articles | {time.perf_counter() - t0:.1f}s")
    return {"selected_articles": [a.model_dump() for a in selected]}


# ── Node: write ────────────────────────────────────────────────────────────────

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
        print(f"  [write] Done: {post_path.name} | {time.perf_counter() - t0:.1f}s")
        return {
            "newsletter_content": content,
            "post_path": post_path,
            "errors": errors,
        }
    except Exception as e:
        msg = f"write_newsletter_post failed: {e}"
        print(f"  [write] ! {msg}")
        errors.append(msg)
        return {"post_path": None, "newsletter_content": "", "errors": errors}


# ── Node: publish ──────────────────────────────────────────────────────────────

def publish_node(state: NewsletterState) -> dict:
    """Commit + push the post, then update state."""
    t0 = time.perf_counter()
    post_path = state.get("post_path")
    app_state = state["app_state"]
    issue_number = state["issue_number"]
    selected = [AnalyzedItem(**d) for d in state.get("selected_articles", [])]
    errors: list[str] = list(state.get("errors", []))

    if post_path is None:
        print("  [publish] No post to publish — skipping git push")
    else:
        try:
            git_commit_and_push(post_path, issue_number)
        except Exception as e:
            msg = f"git_commit_and_push failed: {e}"
            print(f"  [publish] ! {msg}")
            errors.append(msg)

    # Update state regardless of push success
    mark_published(app_state, selected)
    app_state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(app_state)
    print(f"  [publish] State saved | {time.perf_counter() - t0:.1f}s")
    return {"app_state": app_state, "errors": errors}


# ── Build graph ───────────────────────────────────────────────────────────────

_builder = StateGraph(NewsletterState)
_builder.add_node("fetch", fetch_node)
_builder.add_node("analyze", analyze_node)
_builder.add_node("select", select_node)
_builder.add_node("write", write_node)
_builder.add_node("publish", publish_node)

_builder.set_entry_point("fetch")
_builder.add_edge("fetch", "analyze")
_builder.add_edge("analyze", "select")
_builder.add_edge("select", "write")
_builder.add_edge("write", "publish")
_builder.add_edge("publish", END)

pipeline = _builder.compile()


# ── Public entry point ────────────────────────────────────────────────────────

def run_pipeline() -> Path | None:
    """
    Run the full newsletter pipeline.
    Returns the post Path on success, None if no post was written.
    """
    t0 = time.perf_counter()
    print(f"\n{'=' * 60}")
    print(f"  AI Newsletter Generator — Starting pipeline")
    print(f"{'=' * 60}")

    warmup_ollama()
    app_state = load_state()
    issue_number = get_issue_number(app_state)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    initial: NewsletterState = {
        "app_state": app_state,
        "raw_articles": [],
        "analyzed_articles": [],
        "selected_articles": [],
        "newsletter_content": "",
        "post_path": None,
        "issue_number": issue_number,
        "date_str": date_str,
        "errors": [],
    }

    final = pipeline.invoke(initial)

    elapsed = time.perf_counter() - t0
    post_path = final.get("post_path")
    errors = final.get("errors", [])

    print(f"\n{'=' * 60}")
    if post_path:
        print(f"  Done in {elapsed:.1f}s — Issue #{issue_number}: {post_path.name}")
    else:
        print(f"  Done in {elapsed:.1f}s — No post written (check errors above)")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for err in errors:
            print(f"    - {err}")
    print(f"{'=' * 60}\n")

    return post_path
