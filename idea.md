# What's New in AI — Newsletter Generator

## The Idea

A daily AI newsletter that actually reads everything so you don't have to.

Most AI newsletters are written by humans curating links manually, or by GPT summarizing a fixed set of RSS feeds. This one is different: it fetches from a broad, living source list — RSS feeds, arXiv papers, Twitter/X, and company blogs — analyzes each item with a local model, and publishes a structured Jekyll post to GitHub Pages daily.

The source list itself is maintained by Claude, which reviews it weekly and adds new sources, removes dead ones, and adjusts tier rankings based on what's currently relevant in the AI landscape.

## How It Works

Two independent pipelines — run separately or together.

```
── RESEARCH PIPELINE ──────────────────────────────────────────
  (runs every 6h — content lands in Qdrant immediately)

  sources.json ←── Claude (weekly curation)
       │
       ▼
  fetch_node       RSS / arXiv API / Twitter X API / blogs
       │
       ▼
  analyze_node     Ollama: summarize + relevance score + tags (parallel, 4 workers)
       │
       ▼
  ingest_node ───► Qdrant Cloud  (upsert, idempotent by URL)
                       └── searchable immediately after ingest

── NEWSLETTER PIPELINE ────────────────────────────────────────
  (runs once daily at 07:00 — reads from Qdrant, never re-fetches)

  qdrant_fetch_node   Pull today's articles from Qdrant (already analyzed)
       │
       ▼
  editor_node         Autonomous editor — selects, writes reasoning + TL;DR
       │               per article, drafts editorial note on the issue's theme
       ▼
  write_node          Assemble Jekyll post with editor commentary
       │
       ▼
  publish_node        Git push → GitHub Pages auto-builds
```

**Why two pipelines:** Content is searchable the moment it's fetched. The newsletter
builder pulls from Qdrant, so it benefits from every research run — not just today's.
Each layer can be run and debugged independently.

## Design Choices

**Local model for analysis, Claude for curation.**
Running Ollama remotely on the Minisforum (via Tailscale) means analyzing 50–100 articles per day costs nothing. Claude is reserved for the one task that benefits most from its broader knowledge: deciding which sources are worth following and which have gone stale.

**sources.json as the single source of truth.**
All source management happens in one JSON file. Adding a new blog, removing a dead feed, or bumping a source's tier is a one-line edit — and Claude does it automatically every week.

**LangGraph pipeline.**
Same pattern as the ai-history-blog-generator. Each stage is an explicit node with typed state, making it easy to rerun from any checkpoint or swap out individual steps.

**Runs as a Windows service.**
`python service.py` runs a daily scheduled job via the `schedule` library. For always-on use, configure Windows Task Scheduler to call `python service.py --once` at 07:00.

## Infrastructure

```
Mac  ──Tailscale──►  Minisforum (100.118.247.106)
  │                      └── Ollama: qwen2.5:14b + nomic-embed-text
  │
  └─────────────────►  Qdrant Cloud (free tier)
                            └── collection: ai_articles (768-dim vectors)

Blog: ai-newsletter.aag1091.com (GitHub Pages, whats-new-in-ai repo)
Generator: github.com/aag1091-alt/ai-newsletter
```

## The Editor Agent *(planned)*

The current `select_node` is dumb — it just sorts by relevance score and cuts at top-N. The editor agent replaces it with an LLM that has a personality and genuine editorial judgment.

**What the editor does:**
- Reviews all analyzed articles like an editor reviewing pitches
- Picks what goes in and what gets cut — with explicit reasoning for each decision
- Writes a TL;DR at the end of every selected article
- Drafts a short editorial note at the top of each issue explaining the week's theme and what was left out and why
- Tracks recent issues to avoid covering the same topic two days in a row

**What an article looks like with the editor:**
```markdown
### Claude 4 Safety Evaluations — New Benchmark Suite
*Anthropic Blog · May 9 · ★ Major*

Anthropic released a new evaluation framework for measuring deceptive
alignment in frontier models, open-sourced with three findings that
challenge current RLHF assumptions...

**Why this made the cut:** First serious attempt to operationalize
deceptive alignment as a measurable property. The methodology is
portable to open-source models — this will matter beyond Anthropic.

**TL;DR:** New open-source eval suite for deceptive alignment.
3 findings challenge RLHF. Reproducible on open models.
```

**Editor's personality (system prompt):**
- Skeptical of hype, prefers papers with real benchmarks
- Biased toward open-source impact
- Avoids covering the same topic two issues in a row
- Calls out when something is marketing vs. genuine research

**New files needed:** `generate/editor.py`, updated `schemas.py` (`EditorDecision`, `ArticleVerdict`), updated `graph.py` and `writer.py`.

## Deep Research Layer *(built)*

Articles scoring >= `RESEARCH_THRESHOLD` (default 7/10) or flagged `is_major` get automatic deep research before being stored in Qdrant.

**Research pipeline:** `fetch → analyze → deep_research → ingest`

**Search backend priority:**
1. **Perplexica** — self-hosted on Minisforum in Docker, Academic mode searches arXiv + Semantic Scholar (`PERPLEXICA_URL=http://100.118.247.106:3000`). Unlimited, no API key.
2. **DuckDuckGo** — always-on fallback, no key, no limits

*(Tavily was removed — 1000 searches/month free tier is too low at ~1,200 searches/month at default config)*

**What the research agent does per article:**
1. Ollama generates 2 focused search queries (related papers + reactions/implications)
2. Both queries run in parallel, results deduplicated by URL
3. Ollama synthesizes findings into a 120-150 word `research_notes` paragraph
4. `research_notes` + `research_sources` stored in Qdrant alongside the article

**Perplexica setup on Minisforum** (Docker, optional but recommended for academic depth):
```bash
git clone https://github.com/ItzCrazyKns/Perplexica.git
cd Perplexica
cp sample.config.toml config.toml
# Set OLLAMA_API_URL=http://host.docker.internal:11434 in config.toml
docker compose up -d
# Then set PERPLEXICA_URL=http://100.118.247.106:3000 in .env
```

## Knowledge Base & Chatbot

The Qdrant collection grows with every newsletter run. On top of it:

- `search.py` — CLI semantic search with filters (source, date range, tags, major-only)
- `chatbot.py` — RAG chatbot: question → embed → Qdrant top-k → Ollama answers with citations

**Planned:** Web UI for the chatbot (FastAPI + simple frontend), hybrid search (sparse + dense vectors).

## Sources

- **Tier 1 (must-read):** Anthropic, OpenAI, DeepMind, Meta AI, Mistral, xAI blogs + arXiv cs.AI/LG/CL + key researcher Twitter accounts
- **Tier 2 (good):** Hugging Face, Papers With Code, The Batch, Import AI, broader Twitter
- **Tier 3 (nice):** Conference sites, community blogs (Claude adds these over time)

## Output

Live at [ai-newsletter.aag1091.com](https://ai-newsletter.aag1091.com)

Blog repo: [aag1091-alt/whats-new-in-ai](https://github.com/aag1091-alt/whats-new-in-ai)

Each issue groups content into:
- **Editorial Note** — editor's take on the week's theme *(with editor agent)*
- **Research Highlights** — arXiv papers with summaries + TL;DR
- **Industry News** — company blog posts and announcements + TL;DR
- **From the Community** — notable tweets and discussions
- **Quick Reads** — remaining items as one-liners

## Setup

```bash
cp .env.example .env        # fill in API keys
pip install -r requirements.txt
python -m generate          # run once
python service.py           # run as daily service
python search.py "query"    # search the knowledge base
python chatbot.py           # chat with the knowledge base
```

Set `NEWSLETTER_BLOG_DIR` in `.env` to the path of your local clone of the blog repo.
