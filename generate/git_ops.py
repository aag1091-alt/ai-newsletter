"""
Git operations for the newsletter generator.

Commits the new Jekyll post and pushes to the remote branch.
Only files inside NEWSLETTER_BLOG_DIR are staged — the state file lives
outside the blog repo and is not committed here.
"""

import subprocess
from pathlib import Path

from .config import NEWSLETTER_BLOG_DIR, PUSH_BRANCH


def git_commit_and_push(post_path: Path, issue_number: int) -> None:
    """
    Stage post_path, commit with a descriptive message, and push to PUSH_BRANCH.
    Only stages files that exist inside NEWSLETTER_BLOG_DIR.
    """
    cwd = NEWSLETTER_BLOG_DIR

    # Stage the post file
    try:
        subprocess.run(
            ["git", "add", str(post_path)],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        commit_message = f"Add newsletter issue #{issue_number}"
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  Committed: {post_path.name}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        print(f"  ! Git commit failed: {stderr}")
        return

    # Push to the configured branch
    try:
        result = subprocess.run(
            ["git", "push", "origin", f"HEAD:{PUSH_BRANCH}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"  Pushed to origin/{PUSH_BRANCH}")
        else:
            print(f"  ! Push failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        print("  ! Git push timed out after 60s")
    except Exception as e:
        print(f"  ! Push error: {e}")
