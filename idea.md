# What's New in AI — Newsletter Generator

## The Idea

A daily AI newsletter that actually reads everything so you don't have to.

Most AI newsletters are written by humans curating links manually, or by GPT summarizing a fixed set of RSS feeds. This one is different: it fetches from a broad, living source list — RSS feeds, arXiv papers, Twitter/X, and company blogs — analyzes each item with a local model, and publishes a structured Jekyll post to GitHub Pages daily.

The source list itself is maintained by Claude, which reviews it weekly and adds new sources, removes dead ones, and adjusts tier rankings based on what's currently relevant in the AI landscape.

## How It Works

```
sources.json  ←──── Claude (weekly curation)
     │
     ▼
 fetch_node       Pull from RSS, arXiv API, Twitter/X API, blogs
     │
     ▼
 analyze_node     Ollama: summarize + relevance score + tags (per article, parallel)
     │
     ▼
 select_node      Pick top N by score, deduplicate, filter already-published
     │
     ▼
 write_node       Ollama: group by type, write intro, assemble Jekyll post
     │
     ▼
 publish_node     Git commit + push → GitHub Pages auto-builds
```

## Design Choices

**Local model for analysis, Claude for curation.**
Running Ollama locally means analyzing 50–100 articles per day costs nothing. Claude is reserved for the one task that benefits most from its broader knowledge: deciding which sources are worth following and which have gone stale.

**sources.json as the single source of truth.**
All source management happens in one JSON file. Adding a new blog, removing a dead feed, or bumping a source's tier is a one-line edit — and Claude does it automatically every week.

**LangGraph pipeline.**
Same pattern as the ai-history-blog-generator. Each stage is an explicit node with typed state, making it easy to rerun from any checkpoint or swap out individual steps.

**Runs as a Windows service.**
`python service.py` runs a daily scheduled job via the `schedule` library. For always-on use, configure Windows Task Scheduler to call `python service.py --once` at 07:00.

## Sources

- **Tier 1 (must-read):** Anthropic, OpenAI, DeepMind, Meta AI, Mistral, xAI blogs + arXiv cs.AI/LG/CL + key researcher Twitter accounts
- **Tier 2 (good):** Hugging Face, Papers With Code, The Batch, Import AI, broader Twitter
- **Tier 3 (nice):** Conference sites, community blogs (Claude adds these over time)

## Output

Live at [ai-newsletter.aag1091.com](https://ai-newsletter.aag1091.com)

Blog repo: [aag1091-alt/whats-new-in-ai](https://github.com/aag1091-alt/whats-new-in-ai)

Each issue groups content into:
- **Research Highlights** — arXiv papers with summaries
- **Industry News** — company blog posts and announcements
- **From the Community** — notable tweets and discussions
- **Quick Reads** — remaining items as one-liners

## Setup

```bash
cp .env.example .env        # fill in API keys
pip install -r requirements.txt
python -m generate          # run once
python service.py           # run as daily service
```

Set `NEWSLETTER_BLOG_DIR` in `.env` to the path of your local clone of the blog repo.
