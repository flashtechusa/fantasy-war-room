"""ESPN Draft Sync Diagnostics.

The open question about ESPN sync is not whether it works but whether it keeps
up: during a live draft, do picks reach us fast enough to matter? That is not
answerable from the outside, so every sync attempt records what happened and
this module keeps the last few for inspection.

What is deliberately *not* recorded: cookies, request headers, ESPN payloads,
player names from ESPN. The record holds counts, timings and already-redacted
error text -- enough to tell whether ESPN is keeping up, and nothing that would
turn a debug screen into a credential leak.

State is per-process and in memory. Diagnostics that outlive a restart would
mean another table to migrate for data whose entire value expires in minutes.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..espn.redaction import redact

#: How many attempts to keep per draft session. A draft polls every ~10s, so
#: this is roughly the last four minutes -- long enough to see a stall.
HISTORY_LIMIT = 25


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SyncAttempt:
    """One poll of ESPN, successful or not."""

    at: datetime = field(default_factory=_now)
    ok: bool = False
    #: Which code path answered: espn_api | espn_draft_detail | none.
    source: str = ""
    #: Already redacted URL or a description of the library call.
    endpoint: str = ""
    latency_ms: float = 0.0
    #: Counts only. Never the picks themselves.
    espn_pick_count: int = 0
    espn_latest_pick: int = 0
    local_pick_count: int = 0
    local_latest_pick: int = 0
    new_picks: int = 0
    espn_draft_complete: bool = False
    espn_draft_in_progress: bool = False
    library_pick_count: int = 0
    direct_pick_count: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        data["at"] = self.at.isoformat()
        return data


class DraftDiagnostics:
    """Recent sync attempts, keyed by draft session id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[int, deque[SyncAttempt]] = {}

    def record(self, draft_id: int, attempt: SyncAttempt) -> SyncAttempt:
        attempt.error = redact(attempt.error) if attempt.error else ""
        attempt.endpoint = redact(attempt.endpoint) if attempt.endpoint else ""
        with self._lock:
            history = self._history.setdefault(draft_id, deque(maxlen=HISTORY_LIMIT))
            history.append(attempt)
        return attempt

    def history(self, draft_id: int) -> list[SyncAttempt]:
        with self._lock:
            return list(self._history.get(draft_id, ()))

    def last_success(self, draft_id: int) -> SyncAttempt | None:
        return next((a for a in reversed(self.history(draft_id)) if a.ok), None)

    def last_error(self, draft_id: int) -> SyncAttempt | None:
        return next((a for a in reversed(self.history(draft_id)) if a.error), None)

    def clear(self, draft_id: int | None = None) -> None:
        with self._lock:
            if draft_id is None:
                self._history.clear()
            else:
                self._history.pop(draft_id, None)

    def report(self, draft_id: int, *, poll_interval: int, sync_enabled: bool) -> dict:
        """The diagnostics payload. Contains no credentials and no payloads."""
        history = self.history(draft_id)
        latest = history[-1] if history else None
        success = self.last_success(draft_id)
        failure = self.last_error(draft_id)

        successes = [a for a in history if a.ok]
        latencies = [a.latency_ms for a in successes if a.latency_ms > 0]

        seconds_since_success = None
        if success is not None:
            seconds_since_success = round((_now() - success.at).total_seconds(), 1)

        return {
            "draft_session_id": draft_id,
            "polling": {
                "enabled": sync_enabled,
                "interval_seconds": poll_interval,
                "attempts_recorded": len(history),
                "history_limit": HISTORY_LIMIT,
            },
            "endpoint": {
                "url": latest.endpoint if latest else "",
                "source": latest.source if latest else "",
                "candidates": [
                    {
                        "source": "espn_draft_detail",
                        "description": "GET .../seasons/{season}/segments/0/leagues/"
                        "{league_id}?view=mDraftDetail",
                        "role": "live-draft fallback: returns picks while a draft is running",
                    },
                    {
                        "source": "espn_api",
                        "description": "espn-api League.refresh_draft()",
                        "role": "primary: authoritative once ESPN marks the draft complete",
                    },
                ],
            },
            "picks": {
                "espn_latest_pick": latest.espn_latest_pick if latest else 0,
                "local_latest_pick": latest.local_latest_pick if latest else 0,
                "espn_pick_count": latest.espn_pick_count if latest else 0,
                "local_pick_count": latest.local_pick_count if latest else 0,
                "new_picks_last_sync": latest.new_picks if latest else 0,
                "new_picks_detected": bool(latest and latest.new_picks > 0),
                "behind_by": max(
                    0,
                    (latest.espn_latest_pick - latest.local_latest_pick) if latest else 0,
                ),
                "espn_draft_complete": latest.espn_draft_complete if latest else False,
                "espn_draft_in_progress": latest.espn_draft_in_progress if latest else False,
                "library_pick_count": latest.library_pick_count if latest else 0,
                "direct_pick_count": latest.direct_pick_count if latest else 0,
            },
            "response": {
                "last_success_at": success.at.isoformat() if success else None,
                "seconds_since_last_success": seconds_since_success,
                "last_latency_ms": latest.latency_ms if latest else 0.0,
                "average_latency_ms": (
                    round(sum(latencies) / len(latencies), 1) if latencies else 0.0
                ),
                "max_latency_ms": round(max(latencies), 1) if latencies else 0.0,
                "success_rate": (
                    round(len(successes) / len(history), 3) if history else None
                ),
            },
            "last_error": (
                {"at": failure.at.isoformat(), "detail": failure.error} if failure else None
            ),
            "recent": [a.as_dict() for a in history[-10:]],
        }


#: Process-wide instance. Draft polling is a single-process concern.
diagnostics = DraftDiagnostics()
