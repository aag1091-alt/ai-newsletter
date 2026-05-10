"""
RAG chatbot over the AI knowledge base.

Retrieves relevant articles from Qdrant, then uses Ollama to answer
with sources cited. The knowledge base grows daily as the newsletter runs.

Usage:
  python chatbot.py
  python chatbot.py --top 8          # retrieve more context
  python chatbot.py --days 30        # only use articles from last 30 days
"""

import argparse
from datetime import datetime, timezone, timedelta

import ollama

from generate.config import OLLAMA_HOST, OLLAMA_MODEL, QDRANT_COLLECTION
from generate.ingestor import _client, embed, collection_count
from qdrant_client.models import Filter, FieldCondition, Range

_ollama = ollama.Client(host=OLLAMA_HOST)


def retrieve(query: str, top: int, days: int | None) -> list[dict]:
    client = _client()
    vector = embed(query)
    conditions = []
    if days:
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        conditions.append(FieldCondition(key="published_timestamp", range=Range(gte=cutoff)))

    results = client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=vector,
        query_filter=Filter(must=conditions) if conditions else None,
        limit=top,
        with_payload=True,
    )
    return [{"score": r.score, **r.payload} for r in results]


def build_context(articles: list[dict]) -> str:
    parts = []
    for i, a in enumerate(articles, 1):
        parts.append(
            f"[{i}] {a['title']} ({a.get('published', '')[:10]})\n"
            f"Source: {a.get('source_name', '')} ({a.get('source_type', '')})\n"
            f"Tags: {', '.join(a.get('tags', []))}\n"
            f"Summary: {a.get('summary', '')}\n"
            f"URL: {a['url']}"
        )
    return "\n\n".join(parts)


def ask(question: str, top: int, days: int | None) -> tuple[str, list[dict]]:
    articles = retrieve(question, top, days)
    if not articles:
        return "I couldn't find any relevant articles in the knowledge base for that question.", []

    context = build_context(articles)
    prompt = (
        f"You are an AI research assistant with access to a curated knowledge base of AI news and papers.\n\n"
        f"Use ONLY the articles below to answer the question. Cite sources by their [number].\n"
        f"If the articles don't contain enough information, say so clearly.\n\n"
        f"ARTICLES:\n{context}\n\n"
        f"QUESTION: {question}\n\n"
        f"Answer concisely with citations:"
    )

    response = _ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"num_predict": 512, "temperature": 0.2},
    )
    return response.message.content.strip(), articles


def print_sources(articles: list[dict]) -> None:
    print("\n  Sources:")
    for i, a in enumerate(articles, 1):
        major = " ★" if a.get("is_major") else ""
        print(f"  [{i}] {a['title'][:70]}{major}")
        print(f"       {a.get('source_name', '')} · {a.get('published', '')[:10]} · {a['url']}")


def main():
    parser = argparse.ArgumentParser(description="Chat with your AI knowledge base")
    parser.add_argument("--top", type=int, default=6, help="Articles to retrieve per query (default: 6)")
    parser.add_argument("--days", type=int, help="Restrict to articles from last N days")
    args = parser.parse_args()

    total = collection_count()
    scope = f"last {args.days} days" if args.days else "all time"
    print(f"\n{'═' * 55}")
    print(f"  What's New in AI — Knowledge Base Chat")
    print(f"  {total:,} articles · scope: {scope}")
    print(f"  Type 'quit' or Ctrl+C to exit")
    print(f"{'═' * 55}\n")

    while True:
        try:
            question = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not question:
            continue
        if question.lower() in {"quit", "exit", "bye"}:
            print("Bye!")
            break

        print("\nSearching knowledge base...", flush=True)
        answer, articles = ask(question, top=args.top, days=args.days)

        print(f"\nAssistant: {answer}")
        if articles:
            print_sources(articles)
        print()


if __name__ == "__main__":
    main()
