"""Sleeper as an isolated, optional projection source.

Two properties matter most and are asserted hard here:

1. **OFF changes nothing.** With the toggle off, storing Sleeper data leaves the
   board byte-for-byte what it was -- the Sleeper source is disabled and never
   enters the default blend.
2. **ON re-scores under the league's rules.** With the toggle on, a player's
   projected points equal `LeagueScoring.score(sleeper_raw)` for this league --
   Sleeper's own point totals are never used, and no other source is blended in.
"""

from __future__ import annotations

import httpx
import pytest

from app.models import PlayerProjection, ProjectionSource
from app.projections import sleeper
from app.services import board as board_service
from app.services import projections as projection_service


# --- unit: the parser / stat map -------------------------------------------


def _sleeper_payload(*players: dict) -> list[dict]:
    return list(players)


def test_parse_keys_raw_stats_by_espn_stat_id():
    rows = sleeper.parse_projections(
        _sleeper_payload(
            {
                "player": {"first_name": "Josh", "last_name": "Allen", "position": "QB", "team": "BUF"},
                "stats": {
                    "pass_yd": 4200.0,
                    "pass_td": 33.0,
                    "pass_int": 12.0,
                    "rush_yd": 520.0,
                    "rush_td": 6.0,
                    # These must be discarded, not stored as stats:
                    "pts_ppr": 320.5,
                    "pts_std": 320.5,
                    "gp": 17,
                },
            }
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Josh Allen"
    assert row.position == "QB"
    # Keyed by ESPN stat id, as strings, matching how ESPN projections store.
    assert row.raw_stats == {"3": 4200.0, "4": 33.0, "20": 12.0, "24": 520.0, "25": 6.0}
    # The precomputed point totals were dropped.
    assert "pts_ppr" not in row.raw_stats
    assert row.projected_games == 17.0


def test_parse_ignores_unmappable_and_empty():
    rows = sleeper.parse_projections(
        _sleeper_payload(
            {"player": {"first_name": "No", "last_name": "Stats", "position": "K"}, "stats": {"fgm": 20}},
            {"player": {"first_name": "", "last_name": ""}, "stats": {"rec": 5}},  # no name -> dropped
        )
    )
    # The kicker has only unmapped stats -> empty raw_stats (still returned, but
    # store_projections will skip it, so K/DST fall back to the existing source).
    assert len(rows) == 1
    assert rows[0].raw_stats == {}


def test_client_fetches_via_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/projections/nfl/2026" in str(request.url)
        return httpx.Response(
            200,
            json=[
                {
                    "player": {"first_name": "Bijan", "last_name": "Robinson", "position": "RB", "team": "ATL"},
                    "stats": {"rush_yd": 1200.0, "rush_td": 10.0, "rec": 55.0, "rec_yd": 480.0},
                }
            ],
        )

    client = sleeper.SleeperProjectionsClient(season=2026, transport=httpx.MockTransport(handler))
    players = client.projections()
    assert players[0].name == "Bijan Robinson"
    assert players[0].raw_stats == {"24": 1200.0, "25": 10.0, "53": 55.0, "42": 480.0}


# --- board behaviour: OFF is unchanged, ON re-scores -----------------------


def _points(engine, player) -> float:
    """A player's projected fantasy points as the engine computed them."""
    return engine._points[player.espn_player_id]  # noqa: SLF001 - test introspection


@pytest.fixture
def a_player(session, imported_league):
    from app.models import Player

    return (
        session.query(Player)
        .filter(
            Player.season == imported_league.season,
            Player.source == imported_league.source,
        )
        .first()
    )


def _add_sleeper_projection(session, player, raw_stats):
    session.add(
        PlayerProjection(
            player_id=player.id,
            source_key=sleeper.SOURCE_KEY,
            raw_stats=raw_stats,
            source_points=None,
        )
    )
    # Register the source the way import does: present but DISABLED.
    session.add(
        ProjectionSource(key=sleeper.SOURCE_KEY, label="Sleeper projections", weight=1.0, enabled=False)
    )
    session.commit()


def test_off_is_unchanged_when_sleeper_data_exists(session, imported_league, a_player):
    board_service.clear_cache()
    baseline = _points(board_service.build_engine(session, imported_league), a_player)

    # Distinctive stat line that scores very differently from the demo default.
    _add_sleeper_projection(session, a_player, {"3": 6000.0, "4": 55.0, "24": 900.0, "25": 12.0})

    board_service.clear_cache()
    after = _points(board_service.build_engine(session, imported_league), a_player)
    # OFF: the disabled Sleeper source never enters the default blend.
    assert after == baseline


def test_on_rescopes_to_sleeper_under_league_rules(session, imported_league, a_player):
    raw = {"3": 6000.0, "4": 55.0, "24": 900.0, "25": 12.0, "53": 40.0, "42": 300.0}
    _add_sleeper_projection(session, a_player, raw)

    expected = board_service.league_scoring(imported_league).score(raw, a_player.position)

    board_service.clear_cache()
    off = _points(board_service.build_engine(session, imported_league), a_player)
    board_service.clear_cache()
    on = _points(
        board_service.build_engine(session, imported_league, active_source="sleeper"), a_player
    )

    assert on == pytest.approx(expected, abs=0.01)
    # And it genuinely changed the number -- the test would pass vacuously if it didn't.
    assert on != pytest.approx(off, abs=0.01)


def test_on_source_label_is_advertised(session, imported_league, a_player):
    _add_sleeper_projection(session, a_player, {"3": 5000.0, "4": 40.0})
    board_service.clear_cache()
    engine = board_service.build_engine(session, imported_league, active_source="sleeper")
    assert engine.projection_source == "sleeper"


# --- import path: stored, matched, and isolated ----------------------------


def test_import_sleeper_stores_matched_rows_disabled(session, imported_league, monkeypatch):
    from app.models import Player

    names = [
        p.name
        for p in session.query(Player)
        .filter(Player.season == imported_league.season, Player.source == imported_league.source)
        .limit(3)
        .all()
    ]

    fake_players = [
        sleeper.SleeperPlayer(name=n, position="RB", pro_team="", raw_stats={"24": 1000.0, "25": 8.0})
        for n in names
    ]

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def projections(self, week=None):
            return fake_players

    monkeypatch.setattr(projection_service, "SleeperProjectionsClient", FakeClient)

    report = projection_service.import_sleeper(session, imported_league)
    session.commit()

    assert report["matched"] >= 1
    source = session.query(ProjectionSource).filter_by(key="sleeper").first()
    # Isolated: stored but never enabled for the default blend.
    assert source.enabled is False
    stored = session.query(PlayerProjection).filter(
        PlayerProjection.source_key == "sleeper"
    ).count()
    assert stored >= 1


# --- API: toggle, status, and the source the board advertises --------------


def test_toggle_status_and_board_source(client):
    assert client.post("/api/league/import").status_code in (200, 201)

    from app.db import session_scope
    from app.models import Player

    # Seed a Sleeper projection so turning the toggle on skips the network.
    with session_scope() as s:
        player = s.query(Player).first()
        s.add(
            PlayerProjection(
                player_id=player.id,
                source_key="sleeper",
                raw_stats={"3": 5000.0, "4": 40.0},
                source_points=None,
            )
        )
        s.add(
            ProjectionSource(
                key="sleeper", label="Sleeper projections", weight=1.0, enabled=False
            )
        )

    status_body = client.get("/api/league/projections/sleeper").json()
    assert status_body["use_sleeper_projections"] is False
    assert status_body["sleeper"]["imported"] is True

    # Default board is on the default source, not Sleeper.
    meta = client.get("/api/draft/state").json()["meta"]
    assert meta["projection_source"] != "sleeper"

    on = client.post(
        "/api/league/projections/sleeper/toggle", json={"enabled": True}
    ).json()
    assert on["use_sleeper_projections"] is True

    # And now the board says so.
    meta_on = client.get("/api/draft/state").json()["meta"]
    assert meta_on["projection_source"] == "sleeper"

    off = client.post(
        "/api/league/projections/sleeper/toggle", json={"enabled": False}
    ).json()
    assert off["use_sleeper_projections"] is False
    meta_off = client.get("/api/draft/state").json()["meta"]
    assert meta_off["projection_source"] != "sleeper"
