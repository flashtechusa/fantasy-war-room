"""Importing results, not just forecasts.

Every stat the app read was filtered to statSourceId == 1 -- ESPN's code for a
projection. Results are statSourceId == 0, sitting in the same payload we were
already fetching, and nothing read them. That is why six weeks of evidence could
not reach any rating in the app.
"""

from __future__ import annotations

from app.espn.client import EspnClient
from app.models import utcnow

TARGETS, CARRIES = "58", "23"


def _entry(stats):
    """A minimal ESPN player payload carrying the stat entries given."""
    return {"stats": stats}


class _Reader(EspnClient):
    """Just the parsing, with no connection involved."""

    def __init__(self, season=2026):
        self.season = season


def test_actuals_and_projections_are_told_apart():
    reader = _Reader()
    player = _entry([
        # The season projection written in August.
        {"statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0,
         "seasonId": 2026, "appliedTotal": 136.0, "stats": {TARGETS: 90}},
        # What he has actually done.
        {"statSourceId": 0, "statSplitTypeId": 0, "scoringPeriodId": 0,
         "seasonId": 2026, "appliedTotal": 73.0, "stats": {TARGETS: 32, "210": 6}},
    ])

    raw, points, games = reader._season_actuals(player)      # noqa: SLF001
    assert points == 73.0, "the result, not the forecast"
    assert games == 6
    assert raw[TARGETS] == 32

    proj_raw, proj_points, _ = reader._season_projection(player)      # noqa: SLF001
    assert proj_points == 136.0, "the projection must still be the projection"
    assert proj_raw[TARGETS] == 90


def test_a_player_with_no_results_yet_reports_nothing():
    reader = _Reader()
    player = _entry([
        {"statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0,
         "seasonId": 2026, "appliedTotal": 136.0, "stats": {TARGETS: 90}},
    ])
    raw, points, games = reader._season_actuals(player)      # noqa: SLF001
    assert (raw, points, games) == ({}, 0.0, 0.0)


def test_last_seasons_results_are_not_this_seasons():
    reader = _Reader(season=2026)
    player = _entry([
        {"statSourceId": 0, "statSplitTypeId": 0, "scoringPeriodId": 0,
         "seasonId": 2025, "appliedTotal": 210.0, "stats": {TARGETS: 140}},
    ])
    assert reader._season_actuals(player)[1] == 0.0      # noqa: SLF001


def test_weekly_results_come_back_per_week():
    reader = _Reader()
    player = _entry([
        {"statSourceId": 0, "statSplitTypeId": 1, "scoringPeriodId": 4,
         "seasonId": 2026, "appliedTotal": 17.9, "stats": {TARGETS: 8}},
        {"statSourceId": 0, "statSplitTypeId": 1, "scoringPeriodId": 5,
         "seasonId": 2026, "appliedTotal": 17.5, "stats": {TARGETS: 3}},
        # A projection for the same week must not be mistaken for a result.
        {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 5,
         "seasonId": 2026, "appliedTotal": 11.0, "stats": {TARGETS: 6}},
    ])
    stats, points = reader._weekly_actuals(player)      # noqa: SLF001
    assert points == {4: 17.9, 5: 17.5}
    assert stats[4][TARGETS] == 8


class TestTheRecentWindow:
    """The blend asks "how has he looked lately", so the import aggregates it."""

    def test_only_the_last_three_games_are_counted_as_recent(self, drafted_league):
        from app.db import session_scope
        from app.models import Player, PlayerActuals
        from app.services.importer import _store_actuals      # noqa: SLF001

        class _Record:
            actual_stats = {TARGETS: 32}
            actual_points = 73.0
            games_played = 6
            weekly_actual_points = {1: 12.0, 2: 9.1, 3: 1.7, 4: 17.9, 5: 17.5, 6: 14.8}
            weekly_actual_stats = {
                1: {TARGETS: 6}, 2: {TARGETS: 6}, 3: {TARGETS: 3},
                4: {TARGETS: 8}, 5: {TARGETS: 3}, 6: {TARGETS: 6},
            }

        with session_scope() as session:
            player = session.query(Player).first()
            assert player is not None, "fixture should have imported players"
            _store_actuals(session, {}, player, _Record(), utcnow())
            session.flush()
            row = session.query(PlayerActuals).filter(
                PlayerActuals.player_id == player.id
            ).one()

            assert row.games_played == 6
            assert row.source_points == 73.0
            assert row.recent_games == 3
            # Weeks 4, 5 and 6 only: 17.9 + 17.5 + 14.8.
            assert row.recent_source_points == 50.2
            assert row.recent_raw_stats[TARGETS] == 17
            assert row.last_week == 6


class TestTheRecentWindowIsActuallyThreeGames:
    """ESPN returns weekly splits only for the period you ask for.

    One import gave one week of results, so "his last three games" collapsed to
    "his last game" -- live, a receiver whose last three read 17.9, 17.5 and 14.8
    was reported as "14.8 lately", and the median that exists to stop one outlier
    defining a player never had three numbers to work with.
    """

    class _Provider:
        """Returns one week of results per call, like ESPN does."""

        source = "espn"
        current_week = 6

        def __init__(self):
            self.weeks_asked: list[int] = []
            self.results = {6: 14.8, 5: 17.5, 4: 17.9}

        def player_pool(self, limit=600, ppr=False, week=None, free_agents_only=False):
            self.weeks_asked.append(week)
            from app.espn.client import PlayerRecord

            record = PlayerRecord(
                espn_player_id=11, name="Khalil Shakir", position="WR", pro_team="BUF"
            )
            points = self.results.get(week)
            if points is not None:
                record.weekly_actual_points = {week: points}
                record.weekly_actual_stats = {week: {"58": 6}}
            return [record]

    def test_the_previous_weeks_are_fetched_and_merged(self):
        from app.config import get_settings
        from app.models import League
        from app.services.importer import _merge_recent_weeks      # noqa: SLF001

        provider = self._Provider()
        records = provider.player_pool(week=6)
        _merge_recent_weeks(
            records, provider, get_settings(),
            League(id=1, espn_league_id=1, season=2026, is_ppr=True), 6,
        )

        # Reaches back past the window's width: the current week is unplayed and
        # a bye can eat another, so three calendar weeks is not three games.
        assert provider.weeks_asked == [6, 5, 4, 3, 2], provider.weeks_asked
        assert records[0].weekly_actual_points == {4: 17.9, 5: 17.5, 6: 14.8}, (
            "one game is not a recent window"
        )

    def test_week_one_asks_for_nothing_extra(self):
        from app.config import get_settings
        from app.models import League
        from app.services.importer import _merge_recent_weeks      # noqa: SLF001

        provider = self._Provider()
        records = provider.player_pool(week=1)
        _merge_recent_weeks(
            records, provider, get_settings(),
            League(id=1, espn_league_id=1, season=2026, is_ppr=True), 1,
        )
        assert provider.weeks_asked == [1], "there is nothing before week 1"

    def test_a_bye_does_not_shorten_the_window(self):
        """The reported case: the Bengals were on bye in week 6.

        Fetching only weeks 6 and 5 left one game, and "lately" meant a single
        afternoon -- exactly what the median was added to prevent.
        """
        from app.config import get_settings
        from app.models import League
        from app.services.importer import _merge_recent_weeks      # noqa: SLF001

        class _WithABye(self._Provider):
            def __init__(self):
                super().__init__()
                # No week 6: their bye. Results run 5, 4, 3.
                self.results = {5: 14.0, 4: 10.0, 3: 5.0}

        provider = _WithABye()
        records = provider.player_pool(week=7)
        _merge_recent_weeks(
            records, provider, get_settings(),
            League(id=1, espn_league_id=1, season=2026, is_ppr=True), 7,
        )
        assert set(records[0].weekly_actual_points) == {3, 4, 5}, (
            "a bye must not cost the window two of its three games"
        )

    def test_a_failed_week_shortens_the_window_rather_than_breaking_it(self):
        from app.config import get_settings
        from app.models import League
        from app.services.importer import _merge_recent_weeks      # noqa: SLF001

        class _Flaky(self._Provider):
            def player_pool(self, limit=600, ppr=False, week=None, free_agents_only=False):
                if week == 5:
                    raise RuntimeError("ESPN said no")
                return super().player_pool(limit, ppr, week, free_agents_only)

        provider = _Flaky()
        records = provider.player_pool(week=6)
        _merge_recent_weeks(
            records, provider, get_settings(),
            League(id=1, espn_league_id=1, season=2026, is_ppr=True), 6,
        )
        assert set(records[0].weekly_actual_points) == {4, 6}, "week 5 missing, rest intact"
