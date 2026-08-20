"""Per-user projection source: ESPN / Sleeper / FantasyPros / consensus.

The properties that make the selector trustworthy:

- The default ("espn") is byte-identical to the board before modes existed.
- A single-source mode a player is *not* covered by falls back to ESPN, never
  to zero -- a thin source degrades gracefully instead of breaking the board.
- Consensus is a per-player equal-weight blend of whatever sources have data,
  never an average against zero.
- A user's FantasyPros key is stored encrypted, decrypted only into their own
  settings, and never returned by the API.
"""

from __future__ import annotations

import pytest

from app.models import PlayerProjection, ProjectionSource
from app.projections import sleeper
from app.projections.fantasypros import FantasyProsPlayer
from app.services import board as board_service
from app.services import projections as projection_service
from app.services import runtime_config


def _points(engine, player) -> float:
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


def _native_raw(session, player) -> dict:
    """The raw stat line the native (demo/espn) source already stores for a player."""
    row = (
        session.query(PlayerProjection)
        .filter(
            PlayerProjection.player_id == player.id,
            PlayerProjection.source_key.in_(("espn", "demo")),
        )
        .first()
    )
    return dict(row.raw_stats or {}) if row else {}


def _add_projection(session, player, source_key, raw_stats, *, enabled=False, label=None):
    session.add(
        PlayerProjection(
            player_id=player.id, source_key=source_key, raw_stats=raw_stats, source_points=None
        )
    )
    if not session.query(ProjectionSource).filter_by(key=source_key).first():
        session.add(
            ProjectionSource(
                key=source_key, label=label or source_key, weight=1.0, enabled=enabled
            )
        )
    session.commit()


# --- engine: mode -> weights ------------------------------------------------


def test_espn_mode_equals_the_default_board(session, imported_league, a_player):
    """"espn" is the native source, identical to the historical default."""
    default = _points(board_service.build_engine(session, imported_league), a_player)
    board_service.clear_cache()
    espn = _points(
        board_service.build_engine(session, imported_league, active_source="espn"), a_player
    )
    assert espn == default


def test_single_source_mode_falls_back_to_espn_when_uncovered(
    session, imported_league, a_player
):
    """A player Sleeper does not cover keeps its native points, not zero."""
    baseline = _points(board_service.build_engine(session, imported_league), a_player)
    board_service.clear_cache()
    # No Sleeper projection stored for this player at all.
    sleeper_mode = _points(
        board_service.build_engine(session, imported_league, active_source="sleeper"), a_player
    )
    assert sleeper_mode == pytest.approx(baseline)
    assert sleeper_mode > 0


def test_consensus_blends_native_and_sleeper_equally(session, imported_league, a_player):
    """Consensus of a player with both sources = score of their 50/50 raw blend."""
    native_raw = _native_raw(session, a_player)
    assert native_raw, "fixture player should already have a native projection"
    # Give the same player a distinct Sleeper line so the blend is observable.
    sleeper_raw = {"3": 6000.0, "4": 45.0, "24": 100.0}
    _add_projection(session, a_player, sleeper.SOURCE_KEY, sleeper_raw, label="Sleeper")

    scoring = board_service.league_scoring(imported_league)
    keys = set(native_raw) | set(sleeper_raw)
    blended = {
        k: 0.5 * float(native_raw.get(k, 0.0)) + 0.5 * float(sleeper_raw.get(k, 0.0))
        for k in keys
    }
    expected = scoring.score(blended, a_player.position)

    board_service.clear_cache()
    engine = board_service.build_engine(session, imported_league, active_source="consensus")
    assert _points(engine, a_player) == pytest.approx(expected, rel=1e-3)


# --- per-user settings + key encryption ------------------------------------


def test_mode_persists_and_resolves(session, client):
    """set_projection_mode stores the choice; resolve reads it back."""
    user = _owner(session)
    runtime_config.set_projection_mode(session, user, "consensus")
    config = runtime_config.user_config(session, user)
    assert runtime_config.resolve_projection_mode(config) == "consensus"
    with pytest.raises(ValueError):
        runtime_config.set_projection_mode(session, user, "nonsense")


def test_fantasypros_key_is_encrypted_and_injected(session, client):
    user = _owner(session)
    runtime_config.set_fantasypros_key(session, user, "SECRET-FP-KEY")
    config = runtime_config.user_config(session, user)
    # Stored as ciphertext, never the raw key.
    assert config.fantasypros_api_key_encrypted
    assert "SECRET-FP-KEY" not in config.fantasypros_api_key_encrypted
    # Decrypted only into this user's own settings.
    settings = runtime_config.settings_for_user(session, user)
    assert settings.fantasypros_api_key == "SECRET-FP-KEY"
    # Clearing removes it.
    runtime_config.set_fantasypros_key(session, user, None)
    assert not runtime_config.has_fantasypros_key(runtime_config.user_config(session, user))


def _owner(session):
    from app.models import User

    return session.query(User).filter(User.username == "tester").first()


# --- API: status, mode, key -------------------------------------------------


class FakeFPClient:
    """A stand-in FantasyPros client covering only the first few players."""

    covered: list = []

    def __init__(self, *a, **k):
        pass

    def projections(self, week="draft"):
        return list(FakeFPClient.covered)


def _seed_sleeper(client):
    from app.db import session_scope
    from app.models import Player

    with session_scope() as s:
        for player in s.query(Player).limit(20).all():
            s.add(
                PlayerProjection(
                    player_id=player.id,
                    source_key="sleeper",
                    raw_stats={"3": 5000.0, "4": 40.0},
                    source_points=None,
                )
            )
        s.add(ProjectionSource(key="sleeper", label="Sleeper", weight=1.0, enabled=False))


def test_status_reports_mode_and_coverage(client):
    assert client.post("/api/league/import").status_code in (200, 201)
    body = client.get("/api/league/projections/status").json()
    assert body["mode"] == "espn"
    assert set(body["modes"]) == {"espn", "sleeper", "fantasypros", "consensus"}
    assert body["fantasypros"]["key_set"] is False
    assert body["sleeper"]["imported"] is False


def test_selecting_sleeper_switches_the_board(client):
    assert client.post("/api/league/import").status_code in (200, 201)
    _seed_sleeper(client)

    on = client.post("/api/league/projections/mode", json={"mode": "sleeper"}).json()
    assert on["mode"] == "sleeper"
    meta = client.get("/api/draft/state").json()["meta"]
    assert meta["projection_source"] == "sleeper"

    off = client.post("/api/league/projections/mode", json={"mode": "espn"}).json()
    assert off["mode"] == "espn"
    meta_off = client.get("/api/draft/state").json()["meta"]
    assert meta_off["projection_source"] != "sleeper"


def test_bad_mode_is_rejected(client):
    assert client.post("/api/league/import").status_code in (200, 201)
    resp = client.post("/api/league/projections/mode", json={"mode": "wat"})
    assert resp.status_code == 400


def test_fantasypros_key_stored_and_coverage_reported(client, monkeypatch):
    assert client.post("/api/league/import").status_code in (200, 201)

    from app.db import session_scope
    from app.models import Player

    with session_scope() as s:
        sample = s.query(Player).filter(Player.position == "QB").limit(3).all()
        FakeFPClient.covered = [
            FantasyProsPlayer(
                name=p.name, position=p.position, pro_team=p.pro_team or "",
                raw_stats={"3": 4800.0, "4": 38.0}, projected_games=17.0,
            )
            for p in sample
        ]
    monkeypatch.setattr(projection_service, "FantasyProsClient", FakeFPClient)

    resp = client.post(
        "/api/league/projections/fantasypros/key",
        json={"api_key": "MY-OWN-KEY", "import_now": True},
    ).json()
    assert resp["key_set"] is True
    # Coverage was reported and the raw key is nowhere in the response.
    assert resp["import"]["matched"] >= 1
    assert "MY-OWN-KEY" not in str(resp)
    assert resp["status"]["fantasypros"]["key_set"] is True
    assert resp["status"]["fantasypros"]["imported"] is True

    # A thin FantasyPros source, once selected, warns rather than pretending.
    warn = client.post(
        "/api/league/projections/mode", json={"mode": "fantasypros"}
    ).json()
    assert warn["mode"] == "fantasypros"
    assert any("FantasyPros" in w for w in warn["warnings"])


def test_existing_install_key_is_recognised_without_re_entry(client, monkeypatch):
    """A key already configured install-wide counts as set (no re-entry needed).

    The status must report key_set=True (usable) but own_key=False (not this
    user's personal key), and selecting FantasyPros must import using that key.
    """
    assert client.post("/api/league/import").status_code in (200, 201)

    # Configure an install-level key the old way (global runtime override).
    from app.db import session_scope
    from app.models import Player

    with session_scope() as s:
        runtime_config.write_overrides(s, {"fantasypros_api_key": "INSTALL-KEY"})
        sample = s.query(Player).filter(Player.position == "RB").limit(4).all()
        FakeFPClient.covered = [
            FantasyProsPlayer(
                name=p.name, position=p.position, pro_team=p.pro_team or "",
                raw_stats={"24": 1200.0, "25": 9.0}, projected_games=17.0,
            )
            for p in sample
        ]
    monkeypatch.setattr(projection_service, "FantasyProsClient", FakeFPClient)

    status = client.get("/api/league/projections/status").json()
    assert status["fantasypros"]["key_set"] is True
    assert status["fantasypros"]["own_key"] is False
    assert status["fantasypros"]["key_source"] == "install"

    # Selecting FantasyPros imports with the install key -- no key was entered.
    picked = client.post(
        "/api/league/projections/mode", json={"mode": "fantasypros"}
    ).json()
    assert picked["mode"] == "fantasypros"
    assert picked["fantasypros"]["imported"] is True
