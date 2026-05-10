#!/usr/bin/env python3
"""
AI Newsletter Generator
=======================
Fetches, analyzes, and publishes a daily AI newsletter to a Jekyll blog.

Usage:
  python -m generate              # run once now
  python -m generate --status     # show current state
  python -m generate --curate     # only refresh sources (runs Claude curator)
  python -m generate --service    # run as a scheduled daemon
"""

import argparse
import sys
from datetime import datetime, timezone

from .config import (
    APP_ENV,
    NEWSLETTER_BLOG_DIR,
    OLLAMA_MODEL,
    PUSH_BRANCH,
    SCHEDULE_TIME,
    SOURCE_REFRESH_DAYS,
)
from .state import load_state


def _print_status() -> None:
    """Print current state summary."""
    state = load_state()
    print(f"\n{'─' * 55}")
    print(f"  AI Newsletter Generator — Status")
    print(f"{'─' * 55}")
    print(f"  Environment   : {APP_ENV}")
    print(f"  Ollama model  : {OLLAMA_MODEL}")
    print(f"  Push branch   : {PUSH_BRANCH}")
    print(f"  Blog dir      : {NEWSLETTER_BLOG_DIR}")
    print(f"  Issues so far : {state.get('issue_number', 0)}")
    print(f"  Published URLs: {len(state.get('published_urls', []))}")
    last_run = state.get("last_run", "never")
    print(f"  Last run      : {last_run}")
    last_refresh = state.get("last_source_refresh", "never")
    print(f"  Last curation : {last_refresh} (every {SOURCE_REFRESH_DAYS} days)")
    schedule_str = state.get("last_run", "not yet")
    print(f"  Daily schedule: {SCHEDULE_TIME}")
    print()


def _run_curate_only() -> None:
    """Force a source curation run regardless of schedule."""
    from .curator import refresh_sources
    from .state import load_state, save_state

    print("Running source curation with Claude CLI...")
    state = load_state()
    # Force refresh by clearing last_source_refresh
    state["last_source_refresh"] = ""
    try:
        refresh_sources(state)
        save_state(state)
        print("Source curation complete.")
    except Exception as e:
        print(f"! Curation failed: {e}")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Newsletter Generator — daily AI news digest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m generate              # generate today's newsletter\n"
            "  python -m generate --status     # show progress\n"
            "  python -m generate --curate     # refresh sources with Claude\n"
            "  python -m generate --service    # run daily at configured time\n"
        ),
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current state and exit",
    )
    parser.add_argument(
        "--curate",
        action="store_true",
        help="Force a source curation run (uses Claude CLI) and exit",
    )
    parser.add_argument(
        "--service",
        action="store_true",
        help="Run as a scheduled daemon (daily at SCHEDULE_TIME)",
    )
    args = parser.parse_args()

    if args.status:
        _print_status()
        return

    if args.curate:
        _run_curate_only()
        return

    if args.service:
        # Delegate to service.py's run_service()
        try:
            from service import run_service
        except ImportError:
            # service.py is at project root, not inside the package
            import importlib.util
            import pathlib
            service_path = pathlib.Path(__file__).parent.parent / "service.py"
            spec = importlib.util.spec_from_file_location("service", service_path)
            service_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(service_mod)
            service_mod.run_service()
        return

    # Default: run pipeline once
    from .graph import run_pipeline
    post_path = run_pipeline()
    if post_path:
        print(f"Newsletter written to: {post_path}")
    else:
        print("Pipeline completed but no post was written.")
        sys.exit(1)


if __name__ == "__main__":
    main()
