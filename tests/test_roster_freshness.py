"""A move made on ESPN has to show up here.

Reported from real use: the user moved a player in the ESPN app and the War Room
kept showing the old lineup. Our stored roster slots only refreshed as a side
effect of the 90-minute *player pool* sweep -- and a slot move on ESPN doesn't
age the player pool at all, so nothing triggered it. The app then computed Auto
Mode moves against a roster ESPN had already changed.

Roster freshness is now its own, much shorter clock, checked in `league_dep` so
it runs before the board is built and every screen agrees with ESPN.
"""

from __future__ import annotations

from datetime import timedelta

from app.models import League, utcnow
from app.services import importer


def _league(age_seconds: float | None) -> League:
    league = League(id=1, espn_league_id=1, season=2026)
    league.imported_at = None if age_seconds is None else utcnow() - timedelta(seconds=age_seconds)
    return league


class _Settings:
    def __init__(self, connected=True):
        self.espn_swid = "{SWID}" if connected else None
        self.espn_s2 = "s2" if connected else None


def test_roster_age_is_measured_from_the_last_import():
    assert importer.roster_age_seconds(_league(None)) is None
    age = importer.roster_age_seconds(_league(120))
    assert age is not None and 110 < age < 130


def test_the_roster_clock_is_much_shorter_than_the_pool_clock():
    # The whole bug: a slot move on ESPN does not age the player pool, so tying
    # roster freshness to it meant waiting up to 90 minutes.
    assert importer.ROSTER_STALE_AFTER <= 5 * 60


def test_a_fresh_roster_is_not_re_pulled(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(5), _Settings()) is False
    assert calls == []


def test_a_stale_roster_is_re_pulled(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(600), _Settings()) is True
    assert len(calls) == 1

    # ...but not again immediately, however many screens load.
    assert importer.maybe_refresh_rosters(None, _league(600), _Settings()) is False
    assert len(calls) == 1


def test_force_ignores_both_timers(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(1), _Settings(), force=True) is True
    assert importer.maybe_refresh_rosters(None, _league(1), _Settings(), force=True) is True
    assert len(calls) == 2, "the Sync now button must never be throttled"


def test_without_cookies_nothing_is_pulled(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    # Demo mode and not-yet-connected accounts must not reach for ESPN.
    assert importer.maybe_refresh_rosters(None, _league(9999), _Settings(connected=False)) is False
    assert importer.maybe_refresh_rosters(
        None, _league(9999), _Settings(connected=False), force=True
    ) is False
    assert calls == []


def test_a_failed_pull_never_breaks_the_screen(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ESPN said no")

    monkeypatch.setattr(importer, "refresh_rosters", boom)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(9999), _Settings()) is False


def test_the_week_screen_reports_its_freshness(drafted_league):
    body = drafted_league.get("/api/season/lineup").json()
    assert "espn_sync" in body
    assert "synced_at" in body["espn_sync"] and "age_seconds" in body["espn_sync"]


def test_sync_now_is_honest_when_there_is_nothing_to_sync(drafted_league):
    # Demo fixture: no ESPN cookies, so the endpoint reports that rather than
    # claiming a sync it never made.
    body = drafted_league.post("/api/season/sync").json()
    assert body["ok"] is False and body["note"]
