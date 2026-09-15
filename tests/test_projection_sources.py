"""Choosing what the rankings run on.

Sources are interchangeable by design -- every one is stored as raw stat lines
and re-scored under the active league's own rules -- so the app has to let
someone pick between them, blend them, and see what each actually covers.

Sleeper's parser is tested against captured shapes rather than the live API:
their projections endpoint is undocumented and unreachable from CI, and the
parser is the part that can silently attach the wrong numbers.
"""

from __future__ import annotations

import pytest

from app.projections.sleeper import SleeperError, parse_projections


def _entry(first: str, last: str, position: str, team: str, stats: dict) -> dict:
    return {
        "player_id": "1234",
        "player": {
            "first_name": first,
            "last_name": last,
            "position": position,
            "team": team,
        },
        "stats": stats,
        "week": 1,
        "season": "2026",
    }


class TestSleeperParsing:
    def test_stat_keys_become_espn_stat_ids(self):
        players = parse_projections(
            [
                _entry(
                    "Test",
                    "Runner",
                    "RB",
                    "SF",
                    {"rush_yd": 1100.0, "rush_td": 9.0, "rec": 45.0, "rec_yd": 380.0},
                )
            ]
        )
        assert len(players) == 1
        assert players[0].raw_stats == {
            "24": 1100.0,  # rushing yards
            "25": 9.0,     # rushing TDs
            "53": 45.0,    # receptions
            "42": 380.0,   # receiving yards
        }

    def test_their_own_point_totals_are_discarded(self):
        """Scored under Sleeper's rules, so keeping them invites misuse."""
        players = parse_projections(
            [_entry("Test", "Passer", "QB", "KC", {"pass_yd": 4200.0, "pts_ppr": 310.5})]
        )
        assert players[0].raw_stats == {"3": 4200.0}

    def test_field_goal_bands_sum_into_one_category(self):
        """Sleeper's three sub-40 bands all live inside ESPN's 0-39 band."""
        players = parse_projections(
            [
                _entry(
                    "Test",
                    "Kicker",
                    "K",
                    "BAL",
                    {"fgm_0_19": 2.0, "fgm_20_29": 6.0, "fgm_30_39": 8.0, "fgm_50p": 3.0},
                )
            ]
        )
        assert players[0].raw_stats["80"] == 16.0
        assert players[0].raw_stats["74"] == 3.0

    def test_unknown_keys_are_ignored_not_guessed(self):
        players = parse_projections(
            [_entry("Test", "Back", "RB", "GB", {"rush_yd": 800.0, "brand_new_stat": 5.0})]
        )
        assert players[0].raw_stats == {"24": 800.0}

    def test_a_defence_is_named_by_its_team(self):
        entry = {
            "player": {"position": "DEF", "team": "PIT"},
            "stats": {"sack": 44.0, "int": 14.0, "pts_allow": 310.0},
        }
        players = parse_projections([entry])
        assert players[0].name == "PIT"
        assert players[0].position == "DST"
        assert players[0].raw_stats["99"] == 44.0

    def test_players_with_no_usable_stats_are_dropped(self):
        assert parse_projections([_entry("Empty", "Guy", "WR", "NYJ", {})]) == []
        assert parse_projections([_entry("Only", "Points", "WR", "NYJ", {"pts_ppr": 90.0})]) == []

    def test_projected_games_is_capped_at_a_season(self):
        players = parse_projections(
            [_entry("Test", "Runner", "RB", "SF", {"rush_yd": 900.0, "gp": 19.0})]
        )
        assert players[0].projected_games == 17.0

    @pytest.mark.parametrize("payload", [{}, {"nothing": "useful"}, [], "text", None])
    def test_a_shape_we_cannot_read_yields_nothing(self, payload):
        assert parse_projections(payload) == []

    def test_a_nested_list_is_still_found(self):
        """Their responses are not contractual; take the list wherever it is."""
        payload = {"projections": [_entry("Test", "Runner", "RB", "SF", {"rush_yd": 5.0})]}
        assert len(parse_projections(payload)) == 1


class TestSourceControls:
    """Turning sources on and off, and seeing what each covers."""

    @pytest.fixture
    def imported(self, client):
        client.post("/api/league/import")
        return client

    def test_sources_report_what_they_cover(self, imported):
        body = imported.get("/api/league/projection-sources").json()
        assert body["pool_size"] > 0

        by_key = {source["key"]: source for source in body["sources"]}
        # The demo import projects the whole pool.
        assert by_key["demo"]["players_covered"] == body["pool_size"]
        assert by_key["demo"]["coverage"] == 1.0

    def test_a_source_can_be_switched_off(self, imported):
        response = imported.patch(
            "/api/league/projection-sources/demo", json={"enabled": False}
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

        by_key = {
            source["key"]: source
            for source in imported.get("/api/league/projection-sources").json()["sources"]
        }
        assert by_key["demo"]["enabled"] is False

    def test_switching_a_source_off_drops_it_from_the_blend(self, imported):
        """The control has to reach the rankings, not just the screen."""
        player_id = imported.get("/api/players?limit=1").json()["players"][0][
            "espn_player_id"
        ]

        def sources_for_player() -> dict[str, bool]:
            body = imported.get(f"/api/players/{player_id}").json()
            return {
                entry["key"]: entry["counts_towards_blend"]
                for entry in body.get("source_projections") or []
            }

        assert sources_for_player().get("demo") is True

        imported.patch("/api/league/projection-sources/demo", json={"enabled": False})

        # Still listed -- the number is still there to look at -- but no longer
        # counted, which is the distinction the player card draws.
        assert sources_for_player().get("demo") is False

    def test_switching_every_source_off_says_so_rather_than_guessing(self, imported):
        """Falling back to a source the user just disabled would look normal.

        That is the failure mode worth refusing: a board rebuilt from data
        somebody explicitly switched off, with nothing on screen to say so.
        """
        for key in ("demo", "espn", "espn_public", "sleeper", "fantasypros"):
            imported.patch(f"/api/league/projection-sources/{key}", json={"enabled": False})

        response = imported.get("/api/players?limit=5")
        assert response.status_code == 409
        assert "switched off" in response.json()["detail"]

    def test_weight_can_be_changed(self, imported):
        response = imported.patch(
            "/api/league/projection-sources/demo", json={"weight": 2.5}
        )
        assert response.json()["weight"] == 2.5

    def test_an_unknown_source_is_a_404(self, imported):
        assert (
            imported.patch(
                "/api/league/projection-sources/nonsense", json={"enabled": True}
            ).status_code
            == 404
        )

    def test_invalid_weights_are_rejected(self, imported):
        assert (
            imported.patch(
                "/api/league/projection-sources/demo", json={"weight": -1}
            ).status_code
            == 422
        )
        assert (
            imported.patch(
                "/api/league/projection-sources/demo", json={"unknown": True}
            ).status_code
            == 422
        )


class TestSleeperRoute:
    def test_it_stores_what_it_matches(self, client, monkeypatch):
        from app.models import PlayerProjection
        from app.projections.sleeper import SleeperProjection
        from app.services import projections as service

        client.post("/api/league/import")

        from app.db import session_scope
        from app.models import Player

        with session_scope() as session:
            ours = session.query(Player).limit(25).all()
            stand_ins = [
                SleeperProjection(
                    name=player.name,
                    position=player.position,
                    pro_team=player.pro_team,
                    raw_stats={"24": 950.0, "53": 42.0},
                )
                for player in ours
            ]

        monkeypatch.setattr(service, "fetch_sleeper", lambda season, week=None: stand_ins)

        report = client.post("/api/league/projections/sleeper").json()
        assert report["matched"] == 25
        assert report["source"] == "sleeper"

        with session_scope() as session:
            stored = (
                session.query(PlayerProjection)
                .filter(PlayerProjection.source_key == "sleeper")
                .count()
            )
            assert stored == 25

    def test_an_upstream_failure_is_reported_not_swallowed(self, client, monkeypatch):
        from app.services import projections as service

        client.post("/api/league/import")

        def _fail(season, week=None):
            raise SleeperError("Sleeper has no projections for 2026 yet.")

        monkeypatch.setattr(service, "fetch_sleeper", _fail)
        response = client.post("/api/league/projections/sleeper")
        assert response.status_code == 502
        assert "no projections" in response.json()["detail"]

    def test_it_works_the_same_on_a_yahoo_league(self, client, monkeypatch):
        """Sources are platform-agnostic; only what is available differs."""
        from app.db import session_scope
        from app.models import League
        from app.projections.sleeper import SleeperProjection
        from app.services import projections as service

        client.post("/api/league/import")
        with session_scope() as session:
            # Same league, relabelled: nothing about a source depends on the
            # platform the league itself came from.
            session.query(League).update({League.source: "yahoo"})

        monkeypatch.setattr(
            service,
            "fetch_sleeper",
            lambda season, week=None: [
                SleeperProjection(
                    name="Nobody Here", position="RB", pro_team="SF", raw_stats={"24": 1.0}
                )
            ],
        )
        response = client.post("/api/league/projections/sleeper")
        assert response.status_code == 200
        assert response.json()["source"] == "sleeper"
