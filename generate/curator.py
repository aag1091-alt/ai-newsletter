"""
Weekly source curation using Claude CLI.

Claude reviews the current sources.json and today's AI landscape, then
suggests new sources to add and stale ones to deactivate. This runs at most
once every SOURCE_REFRESH_DAYS days (tracked in state).

Claude is used here (not Ollama) because source curation benefits from
Claude's web knowledge about what publications are currently active.
"""

import json
import re
from datetime import datetime, timezone

from .config import SOURCE_REFRESH_DAYS, SOURCES_FILE
from .llm import generate_with_claude_cli
from .schemas import CurationResult, Source
from .sources import load_sources, save_sources


def should_refresh(state: dict) -> bool:
    """Return True if last source refresh was more than SOURCE_REFRESH_DAYS ago."""
    last_refresh = state.get("last_source_refresh", "")
    if not last_refresh:
        return True
    try:
        last_dt = datetime.fromisoformat(last_refresh)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - last_dt).days
        return age_days >= SOURCE_REFRESH_DAYS
    except (ValueError, TypeError):
        return True


def _strip_json_fences(text: str) -> str:
    """Strip markdown code fences from Claude's JSON response."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def refresh_sources(state: dict) -> None:
    """
    Use Claude CLI to curate sources.json — add new active sources and
    deactivate stale ones. Updates state['last_source_refresh'] on success.
    """
    print("  Running Claude source curation...")
    sources = load_sources()
    sources_json = json.dumps([s.model_dump() for s in sources], indent=2)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = f"""You are curating the source list for an AI newsletter generator (today: {today}).

Current sources.json:
{sources_json}

Your task:
1. Identify any NEW tier-1 or tier-2 AI news sources that are missing (active blogs, RSS feeds, arXiv categories, or influential Twitter/X accounts from major AI labs or researchers).
2. Identify any sources that appear stale, defunct, or low-quality that should be deactivated.

Return ONLY valid JSON matching this schema (no markdown, no explanation, just JSON):
{{
  "sources_to_add": [
    {{"type": "rss"|"arxiv"|"twitter"|"blog", "name": "...", "url": "...", "query": "...", "handle": "...", "tier": 1|2, "active": true}}
  ],
  "source_names_to_deactivate": ["exact name from current list"],
  "notes": "brief explanation of changes"
}}

Only include sources_to_add entries if you are confident they exist and are active.
Only include source_names_to_deactivate if you are confident the source is defunct.
If no changes are needed, return empty lists."""

    try:
        response = generate_with_claude_cli(prompt)
    except RuntimeError as e:
        print(f"  ! Claude CLI failed: {e} — skipping curation")
        return

    # Parse Claude's response (may be wrapped in markdown code fences)
    try:
        clean = _strip_json_fences(response)
        data = json.loads(clean)
        result = CurationResult(**data)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"  ! Could not parse curation response: {e}")
        print(f"    Response preview: {response[:200]}")
        return

    # Apply changes
    existing_names = {s.name for s in sources}
    added = 0
    for new_source in result.sources_to_add:
        if new_source.name not in existing_names:
            sources.append(new_source)
            existing_names.add(new_source.name)
            added += 1
            print(f"    + Added source: {new_source.name} ({new_source.type})")

    deactivated = 0
    names_to_deactivate = set(result.source_names_to_deactivate)
    for source in sources:
        if source.name in names_to_deactivate:
            source.active = False
            deactivated += 1
            print(f"    - Deactivated: {source.name}")

    save_sources(sources)
    state["last_source_refresh"] = datetime.now(timezone.utc).isoformat()

    print(f"  Curation complete: +{added} added, -{deactivated} deactivated")
    if result.notes:
        print(f"  Notes: {result.notes}")
