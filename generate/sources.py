"""
Source fetching for the AI newsletter generator.

Supports four source types:
  rss      — feedparser-based RSS/Atom fetch
  arxiv    — HTTP to export.arxiv.org Atom API, parsed with ElementTree
  twitter  — tweepy.Client v2 (skipped gracefully if X_BEARER_TOKEN is empty)
  blog     — treated as rss (feedparser handles most blog feeds)

fetch_all() runs all active sources in parallel and deduplicates by URL.
"""

import json
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import feedparser

from .config import (
    MAX_ARTICLES_PER_SOURCE,
    MAX_ARXIV_RESULTS,
    MAX_TWEETS_PER_HANDLE,
    SOURCES_FILE,
    USER_AGENT,
    X_BEARER_TOKEN,
)
from .schemas import ArticleItem, Source

_ATOM_NS = "http://www.w3.org/2005/Atom"


# ── Source loading ─────────────────────────────────────────────────────────────

def load_sources() -> list[Source]:
    """Load and parse sources.json."""
    with open(SOURCES_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return [Source(**item) for item in raw]


def save_sources(sources: list[Source]) -> None:
    """Persist sources list back to sources.json."""
    with open(SOURCES_FILE, "w", encoding="utf-8") as f:
        json.dump([s.model_dump() for s in sources], f, indent=2)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_date(date_str: str) -> datetime | None:
    """Best-effort parse of various date formats to an aware UTC datetime."""
    if not date_str:
        return None
    # Try ISO 8601 (arxiv, some RSS)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:19], fmt[:len(date_str[:19])])
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # feedparser gives us a time.struct_time in entry.published_parsed
    return None


def _feedparser_date(entry) -> str:
    """Extract ISO date string from a feedparser entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _is_recent(date_str: str, since: datetime) -> bool:
    """Return True if date_str is after `since`."""
    dt = _parse_date(date_str)
    if dt is None:
        return True  # include if we can't parse
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= since


# ── RSS / blog fetch ───────────────────────────────────────────────────────────

def fetch_rss(source: Source, since: datetime) -> list[ArticleItem]:
    """Fetch articles from an RSS/Atom feed using feedparser."""
    feed = feedparser.parse(
        source.url,
        request_headers={"User-Agent": USER_AGENT},
    )
    articles: list[ArticleItem] = []
    for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE * 3]:
        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()
        if not title or not url:
            continue
        published = _feedparser_date(entry)
        if not _is_recent(published, since):
            continue
        # Extract text: summary or content
        raw_text = ""
        if hasattr(entry, "content") and entry.content:
            raw_text = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            raw_text = entry.summary or ""
        # Strip HTML tags crudely (avoid bs4 import for speed; full parse in analyzer)
        import re
        raw_text = re.sub(r"<[^>]+>", " ", raw_text).strip()[:2000]

        author = ""
        if hasattr(entry, "author"):
            author = entry.author or ""

        articles.append(ArticleItem(
            title=title,
            url=url,
            source_name=source.name,
            source_type=source.type,
            published=published,
            raw_text=raw_text,
            author=author,
        ))
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            break
    return articles


# ── arXiv fetch ────────────────────────────────────────────────────────────────

def fetch_arxiv(source: Source, since: datetime) -> list[ArticleItem]:
    """Fetch papers from the arXiv Atom API."""
    query = source.query or "cat:cs.AI"
    url = (
        f"http://export.arxiv.org/api/query"
        f"?search_query={query}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={MAX_ARXIV_RESULTS}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_bytes = resp.read()
    except Exception as e:
        print(f"  ! arXiv fetch failed for {source.name}: {e}")
        return []

    root = ET.fromstring(xml_bytes)
    ns = _ATOM_NS
    articles: list[ArticleItem] = []

    for entry in root.findall(f"{{{ns}}}entry"):
        title_el = entry.find(f"{{{ns}}}title")
        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""

        id_el = entry.find(f"{{{ns}}}id")
        url_str = (id_el.text or "").strip() if id_el is not None else ""

        summary_el = entry.find(f"{{{ns}}}summary")
        raw_text = (summary_el.text or "").strip()[:2000] if summary_el is not None else ""

        published_el = entry.find(f"{{{ns}}}published")
        published = (published_el.text or "").strip() if published_el is not None else ""

        if not title or not url_str:
            continue
        if published and not _is_recent(published, since):
            continue

        # First author
        author = ""
        author_el = entry.find(f"{{{ns}}}author")
        if author_el is not None:
            name_el = author_el.find(f"{{{ns}}}name")
            if name_el is not None:
                author = (name_el.text or "").strip()

        articles.append(ArticleItem(
            title=title,
            url=url_str,
            source_name=source.name,
            source_type="arxiv",
            published=published or datetime.now(timezone.utc).isoformat(),
            raw_text=raw_text,
            author=author,
        ))
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            break

    return articles


# ── Twitter / X fetch ──────────────────────────────────────────────────────────

def fetch_twitter(source: Source, since: datetime) -> list[ArticleItem]:
    """
    Fetch recent tweets from a handle using tweepy v2.
    Gracefully skips if X_BEARER_TOKEN is not set.
    """
    if not X_BEARER_TOKEN:
        print("  ! X API token not set — skipping Twitter sources")
        return []

    try:
        import tweepy
    except ImportError:
        print("  ! tweepy not installed — skipping Twitter sources")
        return []

    client = tweepy.Client(bearer_token=X_BEARER_TOKEN, wait_on_rate_limit=False)
    handle = source.handle
    if not handle:
        return []

    try:
        user_resp = client.get_user(username=handle, user_fields=["id"])
        if not user_resp.data:
            print(f"  ! Twitter: user @{handle} not found")
            return []
        user_id = user_resp.data.id

        # start_time must be RFC3339
        start_time = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        tweets_resp = client.get_users_tweets(
            id=user_id,
            max_results=MAX_TWEETS_PER_HANDLE,
            start_time=start_time,
            tweet_fields=["created_at", "text", "author_id"],
            exclude=["retweets", "replies"],
        )
    except tweepy.errors.TooManyRequests:
        print(f"  ! Twitter rate limit hit for @{handle} — skipping")
        return []
    except Exception as e:
        print(f"  ! Twitter fetch failed for @{handle}: {e}")
        return []

    if not tweets_resp.data:
        return []

    articles: list[ArticleItem] = []
    for tweet in tweets_resp.data:
        tweet_url = f"https://twitter.com/{handle}/status/{tweet.id}"
        published = tweet.created_at.isoformat() if tweet.created_at else datetime.now(timezone.utc).isoformat()
        articles.append(ArticleItem(
            title=f"@{handle}: {tweet.text[:100]}",
            url=tweet_url,
            source_name=source.name,
            source_type="twitter",
            published=published,
            raw_text=tweet.text[:2000],
            author=f"@{handle}",
        ))
    return articles


# ── Unified fetch ──────────────────────────────────────────────────────────────

def _fetch_one(source: Source, since: datetime) -> list[ArticleItem]:
    """Dispatch to the right fetcher for a single source."""
    try:
        if source.type in ("rss", "blog"):
            return fetch_rss(source, since)
        elif source.type == "arxiv":
            return fetch_arxiv(source, since)
        elif source.type == "twitter":
            return fetch_twitter(source, since)
        else:
            print(f"  ! Unknown source type '{source.type}' for {source.name}")
            return []
    except Exception as e:
        print(f"  ! Error fetching {source.name}: {e}")
        return []


def fetch_all(since: datetime) -> list[ArticleItem]:
    """
    Fetch articles from all active sources in parallel.
    Deduplicates by URL before returning.
    """
    sources = [s for s in load_sources() if s.active]
    print(f"  Fetching from {len(sources)} active sources since {since.date()}...")

    all_articles: list[ArticleItem] = []
    seen_urls: set[str] = set()

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_one, src, since): src for src in sources}
        for future in as_completed(futures):
            src = futures[future]
            try:
                items = future.result()
                for item in items:
                    if item.url not in seen_urls:
                        seen_urls.add(item.url)
                        all_articles.append(item)
                if items:
                    print(f"    {src.name}: {len(items)} article(s)")
            except Exception as e:
                print(f"  ! {src.name} fetch error: {e}")

    print(f"  Total unique articles: {len(all_articles)}")
    return all_articles
