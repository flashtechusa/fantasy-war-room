"""When alert checks run, and what happens when the server misses one."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.engine.schedule import (
    DEFAULT_TZ,
    CheckWindow,
    describe,
    due_now,
    lineup_windows,
    waiver_windows,
    windows_for_league,
)

ET = ZoneInfo(DEFAULT_TZ)


def et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


# 2026-09-13 is a Sunday; the surrounding days are used throughout.
SUNDAY = et(2026, 9, 13, 12, 0)


class TestWaiverWindows:
    def test_the_reminder_lands_the_evening_before(self):
        """Claims must be in before processing, so morning-of is useless."""
        windows = waiver_windows(["WEDNESDAY"])
        assert len(windows) == 1
        assert windows[0].weekday == 1        # Tuesday
        assert windows[0].kind == "waivers"

    def test_a_league_processing_twice_gets_two_reminders(self):
        windows = waiver_windows(["WEDNESDAY", "SUNDAY"])
        assert {w.weekday for w in windows} == {1, 5}   # Tuesday, Saturday

    def test_monday_processing_wraps_back_to_sunday(self):
        assert waiver_windows(["MONDAY"])[0].weekday == 6

    def test_unknown_day_names_are_ignored_not_fatal(self):
        assert waiver_windows(["NOT_A_DAY", "FRIDAY"]) != []
        assert len(waiver_windows(["NOT_A_DAY", "FRIDAY"])) == 1

    def test_no_waiver_days_means_no_waiver_alerts(self):
        assert waiver_windows([]) == []

    def test_the_reason_names_the_actual_processing_day(self):
        """The schedule should not be a black box."""
        assert "Wednesday" in waiver_windows(["WEDNESDAY"])[0].why


class TestMostRecent:
    def test_earlier_today_resolves_to_today(self):
        window = CheckWindow("lineup", 6, 11, 30)     # Sunday 11:30
        assert window.most_recent(SUNDAY) == et(2026, 9, 13, 11, 30)

    def test_later_today_resolves_to_last_week(self):
        window = CheckWindow("lineup", 6, 23, 0)      # Sunday 23:00, not yet
        assert window.most_recent(SUNDAY) == et(2026, 9, 6, 23, 0)

    def test_a_different_weekday_walks_backwards(self):
        window = CheckWindow("waivers", 1, 20, 0)     # Tuesday 20:00
        assert window.most_recent(SUNDAY) == et(2026, 9, 8, 20, 0)


class TestDueNow:
    def _sunday_lineup(self) -> list[CheckWindow]:
        return [CheckWindow("lineup", 6, 11, 30)]

    def test_a_window_that_just_opened_is_due(self):
        due = due_now(self._sunday_lineup(), et(2026, 9, 13, 11, 31), {})
        assert len(due) == 1

    def test_before_the_window_nothing_fires(self):
        """11:00 Sunday: this week's window has not opened yet."""
        due = due_now(
            self._sunday_lineup(),
            et(2026, 9, 13, 11, 0),
            {"lineup": et(2026, 9, 6, 11, 35)},
        )
        assert due == []

    def test_running_twice_only_alerts_once(self):
        windows = self._sunday_lineup()
        now = et(2026, 9, 13, 11, 45)
        assert due_now(windows, now, {}) != []
        assert due_now(windows, now, {"lineup": et(2026, 9, 13, 11, 35)}) == []

    def test_a_missed_window_still_fires_when_the_server_returns(self):
        """A reboot at 11:30 Sunday must not silently skip the week's alert."""
        due = due_now(self._sunday_lineup(), et(2026, 9, 13, 13, 0), {})
        assert len(due) == 1

    def test_but_not_once_it_is_too_late_to_matter(self):
        """Telling someone at 4pm about a 1pm kickoff is not a service."""
        due = due_now(self._sunday_lineup(), et(2026, 9, 13, 16, 0), {})
        assert due == []

    def test_waivers_get_a_longer_catch_up_than_lineups(self):
        """A waiver reminder is still useful hours later; a lineup one is not."""
        window = [CheckWindow("waivers", 1, 20, 0)]   # Tuesday 20:00
        assert due_now(window, et(2026, 9, 8, 23, 30), {}) != []
        assert due_now(window, et(2026, 9, 9, 5, 0), {}) != []

    def test_last_run_from_a_previous_week_does_not_block_this_week(self):
        due = due_now(
            self._sunday_lineup(),
            et(2026, 9, 13, 12, 0),
            {"lineup": et(2026, 9, 6, 11, 35)},
        )
        assert len(due) == 1

    def test_utc_input_is_converted_before_comparing(self):
        """The server clock is not necessarily Eastern; the NFL always is."""
        from datetime import timezone

        # 15:45 UTC on 2026-09-13 is 11:45 EDT -- inside the Sunday window.
        utc_now = datetime(2026, 9, 13, 15, 45, tzinfo=timezone.utc)
        assert due_now(self._sunday_lineup(), utc_now, {}) != []


class TestWindowsForLeague:
    def test_it_combines_waivers_lineups_and_injuries(self):
        kinds = {w.kind for w in windows_for_league(["WEDNESDAY"])}
        assert kinds == {"waivers", "lineup", "injury"}

    def test_disabled_kinds_are_dropped(self):
        windows = windows_for_league(["WEDNESDAY"], enabled={"lineup"})
        assert {w.kind for w in windows} == {"lineup"}

    def test_a_league_with_no_waiver_days_still_gets_lineup_alerts(self):
        kinds = {w.kind for w in windows_for_league(None)}
        assert "lineup" in kinds
        assert "waivers" not in kinds

    def test_lineup_windows_cover_all_three_game_days(self):
        assert {w.weekday for w in lineup_windows()} == {6, 3, 0}


class TestDescribe:
    def test_it_reads_as_plain_english(self):
        lines = describe(waiver_windows(["WEDNESDAY"]))
        assert lines == ["Tuesday 20:00 — waivers (waivers process Wednesday)"]

    def test_every_window_produces_a_line(self):
        windows = windows_for_league(["WEDNESDAY"])
        assert len(describe(windows)) == len(windows)
