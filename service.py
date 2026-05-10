"""
Newsletter service — two schedules running independently.

  Research pipeline  → runs every RESEARCH_INTERVAL_HOURS (default: 6h)
  Newsletter pipeline → runs once daily at SCHEDULE_TIME (default: 07:00)

On Windows:
  python service.py              # foreground daemon (minimize the terminal)

For Windows Task Scheduler (separate tasks):
  Research : python service.py --research-once   (every 6h)
  Newsletter: python service.py --newsletter-once (daily at 07:00)

On macOS / Linux:
  nohup python service.py &
  # Or crontab:
  #   0 */6 * * * cd ~/projects/ai-newsletter && python -m generate --research
  #   0 7   * * * cd ~/projects/ai-newsletter && python -m generate --newsletter

Logs → newsletter_service.log
"""

import argparse
import logging
import time
from pathlib import Path

import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "newsletter_service.log"),
        logging.StreamHandler(),
    ],
)


def research_job() -> None:
    logging.info("Research pipeline starting...")
    try:
        from generate.graph import run_research
        count = run_research()
        logging.info(f"Research done — {count} new articles in Qdrant")
    except Exception as e:
        logging.error(f"Research job failed: {e}", exc_info=True)


def newsletter_job() -> None:
    logging.info("Newsletter pipeline starting...")
    try:
        from generate.graph import run_newsletter
        post_path = run_newsletter()
        if post_path:
            logging.info(f"Newsletter published: {post_path.name}")
        else:
            logging.warning("Newsletter pipeline: no post written")
    except Exception as e:
        logging.error(f"Newsletter job failed: {e}", exc_info=True)


def run_service() -> None:
    from generate.config import SCHEDULE_TIME
    import os
    research_interval = int(os.environ.get("RESEARCH_INTERVAL_HOURS", "6"))

    logging.info(f"Service started — research every {research_interval}h · newsletter daily at {SCHEDULE_TIME}")

    schedule.every(research_interval).hours.do(research_job)
    schedule.every().day.at(SCHEDULE_TIME).do(newsletter_job)

    # Run research immediately on startup so Qdrant isn't empty
    research_job()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Newsletter Service")
    parser.add_argument("--research-once",   action="store_true", help="Run research pipeline once and exit")
    parser.add_argument("--newsletter-once", action="store_true", help="Run newsletter pipeline once and exit")
    parser.add_argument("--once",            action="store_true", help="Run both pipelines once and exit")
    args = parser.parse_args()

    if args.research_once:
        research_job()
    elif args.newsletter_once:
        newsletter_job()
    elif args.once:
        research_job()
        newsletter_job()
    else:
        run_service()
