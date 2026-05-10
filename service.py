"""
Daily newsletter service — runs the pipeline on a configurable schedule.

On Windows:
  python service.py              # foreground daemon (minimize the terminal)

For Windows Task Scheduler (run once per day):
  Action : python C:\\path\\to\\ai-newsletter\\service.py --once
  Trigger: Daily at 07:00

On macOS / Linux:
  nohup python service.py &      # background daemon
  # Or add to crontab:
  #   0 7 * * * cd ~/projects/ai-newsletter && python -m generate

Logs are written to newsletter_service.log alongside this file.
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


def run_job() -> None:
    """Execute one newsletter generation cycle."""
    logging.info("Starting newsletter generation...")
    try:
        from generate.graph import run_pipeline
        post_path = run_pipeline()
        if post_path:
            logging.info(f"Newsletter generated: {post_path}")
        else:
            logging.warning("Pipeline completed but no post was written")
    except Exception as e:
        logging.error(f"Newsletter job failed: {e}", exc_info=True)


def run_service() -> None:
    """Block forever, running run_job() once a day at SCHEDULE_TIME."""
    from generate.config import SCHEDULE_TIME
    logging.info(f"Newsletter service started — runs daily at {SCHEDULE_TIME}")
    schedule.every().day.at(SCHEDULE_TIME).do(run_job)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Newsletter Service")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (useful for Task Scheduler / cron)",
    )
    args = parser.parse_args()

    if args.once:
        run_job()
    else:
        run_service()
