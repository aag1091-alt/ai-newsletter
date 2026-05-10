"""
Qdrant ingestor — embeds articles with Ollama and upserts to Qdrant Cloud.

Each article becomes a point with:
  - vector: nomic-embed-text embedding of title + tags + summary
  - payload: all metadata fields, indexed for fast filtering

Point IDs are UUID5 derived from the article URL so upserts are idempotent
— running the ingestor twice on the same article is safe.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from .config import EMBED_DIM, EMBED_MODEL, OLLAMA_HOST, QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL
from .schemas import AnalyzedItem

_ollama = ollama.Client(host=OLLAMA_HOST)
_qdrant: QdrantClient | None = None

_URL_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # UUID namespace for URLs


def _client() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        if not QDRANT_URL or not QDRANT_API_KEY:
            raise RuntimeError("QDRANT_URL and QDRANT_API_KEY must be set in .env")
        _qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant


def setup_collection() -> None:
    """Create the collection and payload indexes if they don't exist yet."""
    client = _client()
    existing = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION in existing:
        return

    print(f"  [qdrant] Creating collection '{QDRANT_COLLECTION}'...")
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
    )
    for field, schema in [
        ("published_timestamp", PayloadSchemaType.INTEGER),
        ("source_type",         PayloadSchemaType.KEYWORD),
        ("tags",                PayloadSchemaType.KEYWORD),
        ("is_major",            PayloadSchemaType.BOOL),
        ("url",                 PayloadSchemaType.KEYWORD),
        ("relevance",           PayloadSchemaType.INTEGER),
    ]:
        client.create_payload_index(QDRANT_COLLECTION, field, schema)
    print(f"  [qdrant] ✓ Collection ready")


def embed(text: str) -> list[float]:
    response = _ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


def _point_id(url: str) -> str:
    return str(uuid.uuid5(_URL_NAMESPACE, url))


def _to_timestamp(date_str: str) -> int:
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())


def _item_to_point(item: AnalyzedItem) -> PointStruct:
    text = f"{item.title}\n{' '.join(item.tags)}\n{item.summary}"
    vector = embed(text)
    return PointStruct(
        id=_point_id(item.url),
        vector=vector,
        payload={
            "title":               item.title,
            "url":                 item.url,
            "source_name":         item.source_name,
            "source_type":         item.source_type,
            "published":           item.published,
            "published_timestamp": _to_timestamp(item.published),
            "author":              item.author,
            "tags":                item.tags,
            "summary":             item.summary,
            "relevance":           item.relevance,
            "is_major":            item.is_major,
            "research_notes":      item.research_notes,
            "research_sources":    item.research_sources,
        },
    )


def url_exists(url: str) -> bool:
    client = _client()
    results = client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=Filter(must=[FieldCondition(key="url", match=MatchValue(value=url))]),
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    return len(results[0]) > 0


def ingest_articles(items: list[AnalyzedItem]) -> int:
    """Embed and upsert articles to Qdrant. Returns count of new articles ingested."""
    if not items:
        return 0

    setup_collection()
    client = _client()
    points = []

    for item in items:
        try:
            point = _item_to_point(item)
            points.append(point)
        except Exception as e:
            print(f"  [qdrant] ! Failed to embed '{item.title[:50]}': {e}")

    if not points:
        return 0

    client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
    print(f"  [qdrant] ✓ Upserted {len(points)} articles")
    return len(points)


def collection_count() -> int:
    """Return total number of points in the collection."""
    try:
        info = _client().get_collection(QDRANT_COLLECTION)
        return info.points_count or 0
    except Exception:
        return 0
