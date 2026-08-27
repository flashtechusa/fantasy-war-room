"""Entry point the scheduler runs: `python -m app.automode_cycle`.

The Windows task (`scripts/windows/auto-mode.ps1`) calls this on the event-shaped
cadence. It opens a database session, runs one autonomous Auto Mode cycle for
every eligible user, and prints a one-line-per-user summary for the task log.
Exit code is 0 whenever the cycle ran (including "nothing to do"); non-zero only
on an unexpected failure, so the scheduler's own logging shows a real problem.

Safe to run as often as the schedule likes: with the install switch off, or no
eligible users, it does nothing. Credentials never appear in its output.
"""

from __future__ import annotations

import sys

from .db import init_db, session_scope
from .services.automode_runner import run_cycle


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    only_user_id = None
    if argv and argv[0].isdigit():
        only_user_id = int(argv[0])

    init_db()
    with session_scope() as session:
        result = run_cycle(session, only_user_id=only_user_id)

    if not result.get("install_enabled"):
        print("Auto Mode: install switch off -- nothing to do.")
        return 0

    ran = result.get("ran", [])
    if not ran:
        print("Auto Mode: no eligible users -- nothing to do.")
        return 0

    for entry in ran:
        actions = ", ".join(
            f"{a.get('tier')}={a.get('status')}" for a in entry.get("actions", [])
        ) or "no action"
        print(f"Auto Mode: {entry.get('user')}: {actions}")
    return 0


if __name__ == "__main__":      # pragma: no cover - exercised via the task/test
    raise SystemExit(main())
