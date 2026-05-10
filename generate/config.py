import os
from pathlib import Path

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

SCRIPT_DIR = Path(__file__).parent.parent
NEWSLETTER_BLOG_DIR = Path(os.environ.get("NEWSLETTER_BLOG_DIR", str(SCRIPT_DIR / "newsletter-blog")))
POSTS_DIR = NEWSLETTER_BLOG_DIR / "_posts"
STATE_FILE = SCRIPT_DIR / "newsletter_state.json"
SOURCES_FILE = Path(__file__).parent / "sources.json"

APP_ENV = os.environ.get("APP_ENV", "dev").lower()
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.3:70b" if APP_ENV == "prod" else "llama3.2:3b")
PUSH_BRANCH = os.environ.get("PUSH_BRANCH", "main" if APP_ENV == "prod" else "dev")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")

MAX_ARTICLES_PER_SOURCE = int(os.environ.get("MAX_ARTICLES_PER_SOURCE", "5"))
MAX_ARXIV_RESULTS = int(os.environ.get("MAX_ARXIV_RESULTS", "15"))
MAX_TWEETS_PER_HANDLE = int(os.environ.get("MAX_TWEETS_PER_HANDLE", "5"))
TOP_N_ARTICLES = int(os.environ.get("TOP_N_ARTICLES", "12"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))
SCHEDULE_TIME = os.environ.get("SCHEDULE_TIME", "07:00")
SOURCE_REFRESH_DAYS = int(os.environ.get("SOURCE_REFRESH_DAYS", "7"))
USER_AGENT = "AINewsletterBot/1.0 (educational newsletter)"
BLOG_GITHUB_URL = os.environ.get("BLOG_GITHUB_URL", "")
