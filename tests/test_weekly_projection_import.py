"""The pool has to be imported *for a week*, or every weekly number is a guess.

Reported from real use, mid-week-2: "14 of 15 players have no published week-2
projection and are shown as season averages." ESPN only puts a scoring period's
projected splits in the payload when you ask for that period, and the pool import
never asked -- it requested scoringPeriodId 0 and got the season total alone. The
free-agent import did pass a week, which is why exactly one player had a real
number.

Two rules here: the import asks for a week, and a missing week counts as stale
even when the rows are minutes old (otherwise the week rollover leaves the screen
on last week's basis until something else ages out).
"""

from __future__ import annotations

from app.services import importer


class _Recorder:
    """A provider that records what the importer asked it for."""

    source = "espn"
    current_week = 2

    def __init__(self):
        self.calls: list[dict] = []

    def player_pool(self, **kwargs):
        self.calls.append(kwargs)
        return []      # empty is enough: we only care about the request


def _league(season=2026):
    from app.models import League

    return League(id=1, espn_league_id=1, season=season, source="espn", is_ppr=True)


def test_the_pool_is_imported_for_the_current_week(monkeypatch):
    from app.config import get_settings

    provider = _Recorder()
    monkeypatch.setattr(importer, "ensure_projection_sources", lambda s: None)

    class _Boom(Exception):
        pass

    # The pool comes back empty, which the importer refuses -- we only need the
    # request it made before that.
    try:
        importer.import_players(None, _league(), provider, get_settings())
    except Exception:      # noqa: BLE001 - the empty-pool guard, not the thing under test
        pass

    assert provider.calls, "the importer must call the provider"
    assert provider.calls[0]["week"] == 2, (
        "without a week ESPN returns the season total only, and every weekly "
        "number becomes a season average"
    )


def test_an_explicit_week_wins(monkeypatch):
    from app.config import get_settings

    provider = _Recorder()
    monkeypatch.setattr(importer, "ensure_projection_sources", lambda s: None)
    try:
        importer.import_players(None, _league(), provider, get_settings(), week=7)
    except Exception:      # noqa: BLE001
        pass
    assert provider.calls[0]["week"] == 7


def test_a_broken_current_week_does_not_block_the_import(monkeypatch):
    from app.config import get_settings

    class _NoWeek(_Recorder):
        @property
        def current_week(self):
            raise RuntimeError("ESPN said no")

    provider = _NoWeek()
    monkeypatch.setattr(importer, "ensure_projection_sources", lambda s: None)
    try:
        importer.import_players(None, _league(), provider, get_settings())
    except Exception:      # noqa: BLE001
        pass
    # Still imported, just without the weekly splits -- degraded, not broken.
    assert provider.calls and provider.calls[0]["week"] is None


def test_a_missing_week_is_visible_to_the_freshness_check(drafted_league):
    from app.db import session_scope
    from app.models import League

    with session_scope() as session:
        league = session.query(League).first()
        # The fixture imports week 1; week 30 is a stand-in for "the week just
        # rolled over and we hold nothing for it".
        assert importer.has_weekly_projections(session, league, 30) is False
