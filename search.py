"""
Search the AI knowledge base.

Usage:
  python search.py "tool use in LLMs"
  python search.py "GPT-4 safety" --source arxiv --days 7
  python search.py "open source models" --major --top 5
  python search.py "diffusion models" --source arxiv --tag "image generation"
"""

import argparse
from datetime import datetime, timezone, timedelta

from generate.ingestor import _client, embed, collection_count
from generate.config import QDRANT_COLLECTION

from qdrant_client.models import Filter, FieldCondition, MatchValue, Range, MatchAny


def search(
    query: str,
    top: int = 8,
    source_type: str | None = None,
    days: int | None = None,
    major_only: bool = False,
    tag: str | None = None,
) -> list[dict]:
    client = _client()
    query_vector = embed(query)

    conditions = []
    if source_type:
        conditions.append(FieldCondition(key="source_type", match=MatchValue(value=source_type)))
    if days:
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        conditions.append(FieldCondition(key="published_timestamp", range=Range(gte=cutoff)))
    if major_only:
        conditions.append(FieldCondition(key="is_major", match=MatchValue(value=True)))
    if tag:
        conditions.append(FieldCondition(key="tags", match=MatchValue(value=tag)))

    results = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=query_vector,
        query_filter=Filter(must=conditions) if conditions else None,
        limit=top,
        with_payload=True,
    )
    return [
        {"score": round(r.score, 3), **r.payload}
        for r in results
    ]


def print_results(results: list[dict]) -> None:
    if not results:
        print("No results found.")
        return
    for i, r in enumerate(results, 1):
        score_bar = "█" * int(r["score"] * 10)
        major = " ★" if r.get("is_major") else ""
        print(f"\n{i}. [{r['score']:.2f}] {score_bar}{major}")
        print(f"   {r['title']}")
        print(f"   {r.get('source_name', '')} · {r.get('source_type', '')} · {r.get('published', '')[:10]}")
        print(f"   Tags: {', '.join(r.get('tags', []))}")
        print(f"   {r.get('summary', '')[:200]}...")
        print(f"   → {r['url']}")


def main():
    parser = argparse.ArgumentParser(description="Search the AI knowledge base")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--top", type=int, default=8, help="Number of results (default: 8)")
    parser.add_argument("--source", choices=["rss", "arxiv", "twitter", "blog"], help="Filter by source type")
    parser.add_argument("--days", type=int, help="Only articles from last N days")
    parser.add_argument("--major", action="store_true", help="Only major announcements")
    parser.add_argument("--tag", help="Filter by tag")
    args = parser.parse_args()

    total = collection_count()
    print(f"\nSearching {total:,} articles for: \"{args.query}\"\n{'─' * 50}")

    results = search(
        query=args.query,
        top=args.top,
        source_type=args.source,
        days=args.days,
        major_only=args.major,
        tag=args.tag,
    )
    print_results(results)
    print()


if __name__ == "__main__":
    main()
