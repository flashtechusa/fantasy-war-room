"""Phase 1 + 2 -- importing a league and its player pool."""

from __future__ import annotations

from sqlalchemy import select

from app.models import (
    HistoricalDraftPick,
    League,
    LeagueTeam,
    Player,
    PlayerProjection,
    ProjectionSource,
    ScoringRule,
)
from app.services.board import build_engine, league_scoring, league_shape
from app.services.importer import get_active_league, import_league, import_players
from app.services.provider import DemoProvider, EspnProvider, build_provider


class TestProviderSelection:
    def test_demo_mode_uses_the_demo_provider(self):
        from app.config import Settings

        assert isinstance(build_provider(Settings(demo_mode=True)), DemoProvider)

    def test_missing_league_id_falls_back_to_demo(self):
        from app.config import Settings

        assert isinstance(build_provider(Settings(demo_mode=False)), DemoProvider)

    def test_configured_league_uses_espn(self):
        from app.config import Settings

        provider = build_provider(Settings(demo_mode=False, espn_league_id=99))
        assert isinstance(provider, EspnProvider)


class TestLeagueImport:
    def test_league_row_is_created(self, session, imported_league):
        assert imported_league.id is not None
        assert imported_league.name == "War Room Demo League"
        assert imported_league.season == 2026
        assert imported_league.team_count == 12
        assert imported_league.source == "demo"

    def test_roster_settings_are_captured(self, imported_league):
        assert imported_league.roster_slots == {
            "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1
        }
        assert imported_league.bench_slots == 6
        assert imported_league.ir_slots == 1

    def test_draft_settings_are_captured(self, imported_league):
        assert imported_league.draft_type == "SNAKE"
        assert len(imported_league.draft_order) == 12

    def test_waiver_and_playoff_settings_are_captured(self, imported_league):
        assert imported_league.uses_faab is True
        assert imported_league.acquisition_budget == 100
        assert imported_league.playoff_team_count == 6
        assert imported_league.regular_season_weeks == 14

    def test_teams_and_owners_are_imported(self, session, imported_league):
        teams = session.scalars(
            select(LeagueTeam).where(LeagueTeam.league_id == imported_league.id)
        ).all()
        assert len(teams) == 12
        assert all(team.owners for team in teams)
        assert all(team.draft_slot for team in teams)

    def test_scoring_rules_are_imported(self, session, imported_league):
        rules = session.scalars(
            select(ScoringRule).where(ScoringRule.league_id == imported_league.id)
        ).all()
        assert len(rules) >= 15
        reception = next(rule for rule in rules if rule.stat_id == 53)
        assert reception.points == 0.5

    def test_draft_history_is_imported(self, session, imported_league):
        picks = session.scalars(
            select(HistoricalDraftPick).where(
                HistoricalDraftPick.league_id == imported_league.id
            )
        ).all()
        assert picks
        assert all(pick.player_name for pick in picks)

    def test_import_is_idempotent(self, session):
        first = import_league(session)
        second = import_league(session)
        assert first.id == second.id
        assert session.scalar(select(League).where(League.id == first.id)) is not None
        teams = session.scalars(
            select(LeagueTeam).where(LeagueTeam.league_id == second.id)
        ).all()
        assert len(teams) == 12  # not duplicated

    def test_get_active_league_finds_the_import(self, session, imported_league):
        assert get_active_league(session).id == imported_league.id

    def test_no_league_returns_none(self, session):
        assert get_active_league(session) is None


class TestPlayerImport:
    def test_players_are_imported(self, session, imported_league):
        players = session.scalars(
            select(Player).where(Player.season == imported_league.season)
        ).all()
        assert len(players) > 200

    def test_required_player_fields_are_populated(self, session, imported_league):
        players = session.scalars(select(Player)).all()
        for player in players[:40]:
            assert player.name
            assert player.position in {"QB", "RB", "WR", "TE", "K", "DST"}
            assert player.pro_team
            assert player.adp is not None
            assert player.bye_week is not None
            assert player.position_adp is not None
            assert player.espn_rank is not None

    def test_every_position_is_represented(self, session, imported_league):
        positions = {
            player.position for player in session.scalars(select(Player)).all()
        }
        assert positions == {"QB", "RB", "WR", "TE", "K", "DST"}

    def test_position_adp_ranks_within_position(self, session, imported_league):
        rbs = sorted(
            [p for p in session.scalars(select(Player)).all() if p.position == "RB"],
            key=lambda p: p.position_adp,
        )
        assert rbs[0].position_adp == 1.0
        assert rbs[0].adp <= rbs[1].adp

    def test_raw_projections_are_stored_for_rescoring(self, session, imported_league):
        projections = session.scalars(select(PlayerProjection)).all()
        assert projections
        assert all(projection.raw_stats for projection in projections)
        assert all(projection.source_key == "demo" for projection in projections)

    def test_projection_sources_are_registered(self, session, imported_league):
        keys = {source.key for source in session.scalars(select(ProjectionSource)).all()}
        assert {"espn", "demo"} <= keys

    def test_reimporting_players_does_not_duplicate(self, session, imported_league):
        before = len(session.scalars(select(Player)).all())
        import_players(session, imported_league)
        assert len(session.scalars(select(Player)).all()) == before
        assert len(session.scalars(select(PlayerProjection)).all()) == before


class TestEngineFromDatabase:
    def test_engine_builds_from_stored_data(self, engine):
        assert len(engine.players) > 200
        assert engine.shape.team_count == 12
        assert engine.scoring.points_per_reception == 0.5

    def test_projections_are_rescored_under_league_rules(self, engine):
        """A player's points must come from our scoring, not a stored total."""
        player = max(engine.players, key=lambda p: engine.points_for(p.espn_player_id))
        expected = engine.scoring.score(player.raw_stats, player.position)
        assert engine.points_for(player.espn_player_id) == expected

    def test_replacement_levels_exist_for_every_position(self, engine):
        for position in ("QB", "RB", "WR", "TE", "K", "DST"):
            assert engine.replacement.positions[position].replacement_points > 0

    def test_engine_is_cached_between_calls(self, session, imported_league):
        first = build_engine(session, imported_league)
        second = build_engine(session, imported_league)
        assert first is second

    def test_shape_and_scoring_helpers_agree_with_the_league(self, imported_league):
        shape = league_shape(imported_league)
        scoring = league_scoring(imported_league)
        assert shape.roster_size == 15
        assert scoring.format_label == "0.5 PPR"
