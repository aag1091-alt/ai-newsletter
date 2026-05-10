"""
Newsletter post writer.

Uses Ollama to generate a 2-3 sentence intro paragraph, then assembles a
complete Jekyll-formatted newsletter post. Items are grouped by source_type:

  arxiv   → Research Highlights
  rss/blog → Industry News
  twitter  → From the Community
  (rest)  → Quick Reads
"""

from datetime import datetime
from pathlib import Path

from .config import POSTS_DIR
from .llm import _call_ollama_chat
from .schemas import AnalyzedItem


def _write_intro(selected: list[AnalyzedItem], date_str: str, issue_number: int) -> str:
    """Ask Ollama to write a 2-3 sentence newsletter intro."""
    top_titles = "\n".join(f"- {item.title}" for item in selected[:6])
    prompt = (
        f"You are writing the intro paragraph for 'What's New in AI #{issue_number}' "
        f"dated {date_str}. Write 2-3 sentences that capture the overall theme of this "
        f"week's AI news in an engaging, informative tone — no fluff, no filler phrases "
        f"like 'this week in AI'. Just describe what is actually happening based on these "
        f"top stories:\n\n{top_titles}\n\nWrite only the intro paragraph, nothing else."
    )
    messages = [{"role": "user", "content": prompt}]
    result = _call_ollama_chat(messages)
    if not result:
        return f"This issue covers the latest developments in artificial intelligence for {date_str}."
    return result.strip()


def _format_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'May 9, 2025' format."""
    try:
        dt = datetime.fromisoformat(date_str[:10])
        return dt.strftime("%B %-d, %Y")
    except (ValueError, AttributeError):
        return date_str


def _item_to_section_entry(item: AnalyzedItem) -> str:
    """Format a single AnalyzedItem as a section entry."""
    author_str = f" · {item.author}" if item.author else ""
    tags_str = ", ".join(item.tags) if item.tags else ""
    tags_line = f"\n*Tags: {tags_str}*" if tags_str else ""
    return (
        f"### [{item.title}]({item.url})\n"
        f"*{item.source_name}{author_str}*\n\n"
        f"{item.summary}{tags_line}\n"
    )


def _item_to_bullet(item: AnalyzedItem) -> str:
    """Format a single AnalyzedItem as a Quick Reads bullet."""
    # First sentence of summary
    summary_short = item.summary.split(". ")[0].rstrip(".") + "."
    return f"- **[{item.title}]({item.url})** ({item.source_name}) — {summary_short}"


def write_newsletter_post(
    selected: list[AnalyzedItem],
    issue_number: int,
    date_str: str,
) -> tuple[str, Path]:
    """
    Use Ollama to write a newsletter intro, then assemble the full Jekyll post.

    Returns (content_str, post_path).
    The post is written to POSTS_DIR with filename YYYY-MM-DD-ai-newsletter-NNN.md.
    """
    # Group items by source_type
    research: list[AnalyzedItem] = []
    industry: list[AnalyzedItem] = []
    community: list[AnalyzedItem] = []
    quick_reads: list[AnalyzedItem] = []

    for item in selected:
        if item.source_type == "arxiv":
            research.append(item)
        elif item.source_type in ("rss", "blog"):
            industry.append(item)
        elif item.source_type == "twitter":
            community.append(item)
        else:
            quick_reads.append(item)

    # Items beyond 3 per section go to Quick Reads
    _MAX_PER_SECTION = 3
    if len(research) > _MAX_PER_SECTION:
        quick_reads.extend(research[_MAX_PER_SECTION:])
        research = research[:_MAX_PER_SECTION]
    if len(industry) > _MAX_PER_SECTION:
        quick_reads.extend(industry[_MAX_PER_SECTION:])
        industry = industry[:_MAX_PER_SECTION]
    if len(community) > _MAX_PER_SECTION:
        quick_reads.extend(community[_MAX_PER_SECTION:])
        community = community[:_MAX_PER_SECTION]

    print(f"  Writing newsletter intro with Ollama...")
    intro = _write_intro(selected, date_str, issue_number)

    # Collect all tags across selected items
    all_tags: list[str] = []
    seen_tags: set[str] = set()
    for item in selected:
        for tag in item.tags:
            if tag not in seen_tags:
                seen_tags.add(tag)
                all_tags.append(tag)
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in all_tags[:8]) + "]"

    human_date = _format_date(date_str)

    # Build Jekyll front matter
    parts: list[str] = [
        "---",
        f'layout: post',
        f'title: "What\'s New in AI #{issue_number} — {human_date}"',
        f"date: {date_str}",
        f"categories: [newsletter]",
        f"tags: {tags_yaml}",
        f"issue: {issue_number}",
        "---",
        "",
        intro,
        "",
    ]

    # Research Highlights section
    if research:
        parts += ["## Research Highlights", ""]
        for item in research:
            parts += [_item_to_section_entry(item), ""]

    # Industry News section
    if industry:
        parts += ["## Industry News", ""]
        for item in industry:
            parts += [_item_to_section_entry(item), ""]

    # From the Community section
    if community:
        parts += ["## From the Community", ""]
        for item in community:
            author_str = item.author or item.source_name
            parts += [
                f"### {author_str}",
                f"[{item.title}]({item.url})",
                "",
                item.summary,
                "",
            ]

    # Quick Reads section
    if quick_reads:
        parts += ["## Quick Reads", ""]
        for item in quick_reads:
            parts.append(_item_to_bullet(item))
        parts.append("")

    parts += [
        "---",
        f"*Sources curated by Claude · Analysis by local Ollama · [Issue #{issue_number}]*",
    ]

    content = "\n".join(parts)

    # Write to disk
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{date_str}-ai-newsletter-{issue_number:03d}.md"
    post_path = POSTS_DIR / filename
    post_path.write_text(content, encoding="utf-8")
    print(f"  Post written: {post_path.name}")

    return content, post_path
