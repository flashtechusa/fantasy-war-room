"""ESPN's public projections -- the source that makes a Yahoo league usable.

The network call is not exercised here (no network in the suite); what is
exercised is the parser and the storage decision, which are the parts that can
be wrong without anything looking wrong.
"""

from __future__ import annotations

from app.projections.espn_public import PublicProjection, parse_projections


def _entry(player_id: int, name: str, position_id: int, stats: list[dict]) -> dict:
    return {
        "id": player_id,
        "player": {
            "id": player_id,
            "fullName": name,
            "defaultPositionId": position_id,
            "proTeamId": 12,
            "eligibleSlots": [2, 23, 20],
            "stats": stats,
        },
    }


SEASON_PROJECTION = {
    "seasonId": 2026,
    "statSourceId": 1,
    "statSplitTypeId": 0,
    "scoringPeriodId": 0,
    "appliedTotal": 260.0,
    "appliedAverage": 16.25,
    "stats": {"24": 1100.0, "25": 8.0, "53": 45.0, "42": 380.0},
}


class TestParsing:
    def test_season_projection_is_taken(self):
        players = parse_projections(
            {"players": [_entry(1, "Test Back", 2, [SEASON_PROJECTION])]}, 2026
        )
        assert len(players) == 1
        assert players[0].name == "Test Back"
        assert players[0].position == "RB"
        assert players[0].raw_stats["24"] == 1100.0
        assert players[0].projected_games == 16.0

    def test_actuals_and_weekly_splits_are_ignored(self):
        entry = _entry(
            2,
            "Weekly Only",
            2,
            [
                {**SEASON_PROJECTION, "statSourceId": 0},          # actuals
                {**SEASON_PROJECTION, "statSplitTypeId": 1, "scoringPeriodId": 3},
            ],
        )
        assert parse_projections({"players": [entry]}, 2026) == []

    def test_another_season_is_ignored(self):
        entry = _entry(3, "Last Year", 2, [{**SEASON_PROJECTION, "seasonId": 2025}])
        assert parse_projections({"players": [entry]}, 2026) == []

    def test_a_payload_we_cannot_read_yields_nothing(self):
        assert parse_projections({"nothing": "useful"}, 2026) == []


class TestStorage:
    def test_projections_attach_to_matching_players(self, session, imported_league, monkeypatch):
        from app.models import Player, PlayerProjection
        from app.services import projections as service

        ours = session.query(Player).filter(Player.season == imported_league.season).all()
        stand_ins = [
            PublicProjection(
                name=player.name,
                position=player.position,
                pro_team=player.pro_team,
                raw_stats={"24": 900.0, "53": 40.0},
                projected_games=16.0,
            )
            for player in ours[:40]
        ]
        monkeypatch.setattr(service, "fetch_projections", lambda season, limit=900: stand_ins)

        report = service.import_espn_public(session, imported_league)
        session.commit()

        assert report["matched"] == 40
        stored = (
            session.query(PlayerProjection)
            .filter(PlayerProjection.source_key == "espn_public")
            .count()
        )
        assert stored == 40

    def test_a_sole_source_stays_enabled_even_when_partial(
        self, session, imported_league, monkeypatch
    ):
        """A partial blend distorts rankings; a partial *only* source beats none.

        This is the case a Yahoo league lands in: Yahoo publishes no
        projections, so switching this off for low coverage would leave the
        board empty rather than incomplete.
        """
        from app.models import Player, PlayerProjection, ProjectionSource
        from app.services import projections as service

        # Strip the demo projections so this is genuinely the only source.
        session.query(PlayerProjection).delete()
        session.commit()

        ours = session.query(Player).filter(Player.season == imported_league.season).all()
        few = [
            PublicProjection(
                name=player.name,
                position=player.position,
                pro_team=player.pro_team,
                raw_stats={"24": 800.0},
            )
            for player in ours[:5]
        ]
        monkeypatch.setattr(service, "fetch_projections", lambda season, limit=900: few)

        report = service.import_espn_public(session, imported_league)
        session.commit()

        assert report["coverage"] < service.MIN_COVERAGE
        assert report["enabled"] is True
        assert "only projection source" in report["warning"]
        source = (
            session.query(ProjectionSource)
            .filter(ProjectionSource.key == "espn_public")
            .one()
        )
        assert source.enabled is True

    def test_a_partial_source_is_disabled_when_something_else_covers_the_pool(
        self, session, imported_league, monkeypatch
    ):
        from app.models import Player, ProjectionSource
        from app.services import projections as service

        ours = session.query(Player).filter(Player.season == imported_league.season).all()
        few = [
            PublicProjection(
                name=player.name,
                position=player.position,
                pro_team=player.pro_team,
                raw_stats={"24": 800.0},
            )
            for player in ours[:5]
        ]
        monkeypatch.setattr(service, "fetch_projections", lambda season, limit=900: few)

        # The demo import already left a full set of projections in place.
        report = service.import_espn_public(session, imported_league)
        session.commit()

        assert report["enabled"] is False
        source = (
            session.query(ProjectionSource)
            .filter(ProjectionSource.key == "espn_public")
            .one()
        )
        assert source.enabled is False


class TestRoute:
    def test_endpoint_reports_the_upstream_failure(self, client, monkeypatch):
        from app.projections.espn_public import EspnPublicError
        from app.services import projections as service

        client.post("/api/league/import")

        def _fail(season, limit=900):
            raise EspnPublicError("ESPN published no projections for 2026.")

        monkeypatch.setattr(service, "fetch_projections", _fail)
        response = client.post("/api/league/projections/espn-public")
        assert response.status_code == 502
        assert "no projections" in response.json()["detail"]
