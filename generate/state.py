"""
Newsletter state management.

State is persisted as newsletter_state.json in SCRIPT_DIR (the generator
directory, not the Jekyll blog repo). Tracks issue numbers, published URLs,
and timing for scheduled operations.
"""

import json
from pathlib import Path

from .config import STATE_FILE
from .schemas import AnalyzedItem

_DEFAULTS: dict = {
    "issue_number": 0,
    "published_urls": [],
    "last_run": "",
    "last_source_refresh": "",
}

_MAX_PUBLISHED_URLS = 1000


def load_state() -> dict:
    """Load newsletter_state.json or return defaults."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults so new keys are always present
            return {**_DEFAULTS, **data}
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ! Could not read state file ({e}) — using defaults")
    return dict(_DEFAULTS)


def save_state(state: dict) -> None:
    """Persist state to newsletter_state.json."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_issue_number(state: dict) -> int:
    """Increment issue number in state and return the new value."""
    state["issue_number"] = state.get("issue_number", 0) + 1
    return state["issue_number"]


def mark_published(state: dict, items: list[AnalyzedItem]) -> None:
    """
    Add item URLs to published_urls.
    Caps the list at _MAX_PUBLISHED_URLS to avoid unbounded growth (keep newest).
    """
    published: list[str] = state.get("published_urls", [])
    new_urls = [item.url for item in items if item.url not in published]
    published.extend(new_urls)
    # Keep only the most recent N URLs
    if len(published) > _MAX_PUBLISHED_URLS:
        published = published[-_MAX_PUBLISHED_URLS:]
    state["published_urls"] = published


def already_published(state: dict, url: str) -> bool:
    """Return True if this URL has already been included in a past issue."""
    return url in state.get("published_urls", [])
