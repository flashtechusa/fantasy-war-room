"""When to run alert checks, derived from a league's own settings.

Not a fixed calendar: a league that processes waivers on Thursday gets its
reminder on Wednesday night, and one that processes twice a week gets two.
The days come from ESPN via `League.waiver_process_days`.

**Missed windows still fire.** A check is due when its time has passed and it
has not run since -- not when the clock reads exactly 11:30. A server that was
rebooting at 11:30 on Sunday would otherwise skip the most valuable alert of
the week and nobody would know. Lateness is worth far more than silence here,
right up until the moment it stops being actionable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

#: The NFL runs on Eastern time, so every default here is expressed in it
#: regardless of where the server happens to sit.
DEFAULT_TZ = "America/New_York"

WEEKDAYS = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}

#: How long after a window opens it is still worth firing. Past this the
#: information is stale -- telling someone at 4pm that their running back was
#: out at 1pm is not a service.
CATCHUP_HOURS = {
    "lineup": 3,
    "waivers": 12,
    "injury": 6,
}


@dataclass(frozen=True)
class CheckWindow:
    """One recurring moment at which a check should run."""

    kind: str
    weekday: int
    hour: int
    minute: int = 0
    #: Human-readable reason, shown in the settings UI so the schedule is not
    #: a black box.
    why: str = ""

    def most_recent(self, now: datetime) -> datetime:
        """The latest occurrence of this window at or before `now`."""
        days_back = (now.weekday() - self.weekday) % 7
        candidate = (now - timedelta(days=days_back)).replace(
            hour=self.hour, minute=self.minute, second=0, microsecond=0
        )
        if candidate > now:
            candidate -= timedelta(days=7)
        return candidate


def waiver_windows(process_days: list[str], hour: int = 20) -> list[CheckWindow]:
    """A reminder the evening before each day the league processes waivers.

    The night before, not the morning of: claims have to be *in* before
    processing, and nobody wants to be told at 3am that they should have
    bid on someone.
    """
    windows: list[CheckWindow] = []
    for raw in process_days or []:
        index = WEEKDAYS.get(str(raw).strip().upper())
        if index is None:
            continue
        evening_before = (index - 1) % 7
        windows.append(
            CheckWindow(
                kind="waivers",
                weekday=evening_before,
                hour=hour,
                why=f"waivers process {str(raw).title()}",
            )
        )
    return windows


def lineup_windows() -> list[CheckWindow]:
    """Before each slate of games, when a broken lineup can still be fixed."""
    return [
        CheckWindow("lineup", WEEKDAYS["SUNDAY"], 11, 30, why="before the 1pm slate"),
        CheckWindow("lineup", WEEKDAYS["THURSDAY"], 18, 45, why="before Thursday night"),
        CheckWindow("lineup", WEEKDAYS["MONDAY"], 19, 45, why="before Monday night"),
    ]


def injury_windows() -> list[CheckWindow]:
    """Twice a day: practice reports land midweek, inactives land Sunday."""
    return [
        CheckWindow("injury", day, 9, 0, why="morning injury report")
        for day in (WEEKDAYS["WEDNESDAY"], WEEKDAYS["FRIDAY"], WEEKDAYS["SATURDAY"])
    ]


def windows_for_league(
    process_days: list[str] | None,
    *,
    enabled: set[str] | None = None,
) -> list[CheckWindow]:
    """Every window this league should run, filtered by what the user wants."""
    windows = waiver_windows(process_days or []) + lineup_windows() + injury_windows()
    if enabled is not None:
        windows = [w for w in windows if w.kind in enabled]
    return windows


def due_now(
    windows: list[CheckWindow],
    now: datetime,
    last_run: dict[str, datetime],
    *,
    timezone: str = DEFAULT_TZ,
) -> list[CheckWindow]:
    """Windows that have opened and not yet run.

    `last_run` maps a window's kind to when that kind last executed. A window
    fires when its most recent occurrence is newer than the last run of its
    kind, and is still inside the catch-up horizon.
    """
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)

    ready: list[CheckWindow] = []
    for window in windows:
        opened = window.most_recent(local_now)
        age = local_now - opened
        if age > timedelta(hours=CATCHUP_HOURS.get(window.kind, 6)):
            continue  # stale -- the moment to act has passed
        previous = last_run.get(window.kind)
        if previous is not None and previous.astimezone(zone) >= opened:
            continue  # already handled this occurrence
        ready.append(window)

    return ready


def describe(windows: list[CheckWindow], *, timezone: str = DEFAULT_TZ) -> list[str]:
    """Plain-English schedule, so the user can see when to expect alerts."""
    names = {v: k.title() for k, v in WEEKDAYS.items()}
    lines = []
    for window in sorted(windows, key=lambda w: (w.weekday, w.hour, w.minute)):
        clock = f"{window.hour:02d}:{window.minute:02d}"
        suffix = f" ({window.why})" if window.why else ""
        lines.append(f"{names[window.weekday]} {clock} — {window.kind}{suffix}")
    return lines
