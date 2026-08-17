"""Self-update, so a fix doesn't cost you a terminal session.

The app is often running somewhere you only have a browser -- a Codespace open
on a phone -- where "stop the server, git pull, restart" is genuinely painful.
This pulls the branch it was deployed from; if the server is running with
`--reload` (the devcontainer sets that), uvicorn restarts itself and the fix is
live within seconds.

Scope is deliberately narrow: a fast-forward pull of the current branch from
`origin`, with no user-supplied input reaching the command line. It cannot run
arbitrary commands, check out a different ref, or fetch a different remote.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])

REPO_ROOT = Path(__file__).resolve().parents[3]
GIT_TIMEOUT = 60


def _git(*args: str) -> tuple[int, str]:
    """Run a fixed git command in the repo. No shell, no interpolation."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="git is not available in this environment.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="git timed out. Check the network from this machine.",
        ) from exc
    return (completed.returncode, (completed.stdout + completed.stderr).strip())


@router.get("/version")
def version() -> dict:
    """What's currently deployed, and whether anything newer exists."""
    if not (REPO_ROOT / ".git").exists():
        return {"git": False, "detail": "Not a git checkout; self-update unavailable."}

    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, describe = _git("log", "-1", "--pretty=%h %s")
    _, when = _git("log", "-1", "--pretty=%cr")

    behind = 0
    code, _ = _git("fetch", "--quiet", "origin", branch)
    if code == 0:
        counted, output = _git("rev-list", "--count", f"HEAD..origin/{branch}")
        if counted == 0 and output.isdigit():
            behind = int(output)

    return {
        "git": True,
        "branch": branch,
        "commit": describe,
        "committed": when,
        "updates_available": behind,
    }


@router.post("/update")
def update() -> dict:
    """Fast-forward to the latest commit on the current branch."""
    if not (REPO_ROOT / ".git").exists():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Not a git checkout; self-update is unavailable here.",
        )

    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, before = _git("rev-parse", "HEAD")

    code, output = _git("pull", "--ff-only", "origin", branch)
    if code != 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Could not update: {output.splitlines()[-1] if output else 'git pull failed'}. "
                "This usually means local edits are in the way."
            ),
        )

    _, after = _git("rev-parse", "HEAD")
    _, commit = _git("log", "-1", "--pretty=%h %s")
    changed = before != after

    return {
        "updated": changed,
        "branch": branch,
        "commit": commit,
        "detail": (
            "Updated. The server restarts itself in a few seconds -- pull down to "
            "refresh this page."
            if changed
            else "Already on the latest version."
        ),
    }
