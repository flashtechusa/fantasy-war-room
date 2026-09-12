"""Auto Mode -- staged and gated three ways.

The properties that keep autopilot safe:
- Off by default: no plan runs until the install switch, the per-account
  capability, and the user's own opt-in all line up.
- Lineup writing is live (reversible, own-team-only) and user-triggered; waivers
  and trades stay dry-run until each payload is captured.
- The lineup planner produces real start/sit moves from the optimal lineup.
- Only the owner flips the install switch or grants the capability.
"""

from __future__ import annotations

from app.services import automode


def _tester_id(client) -> int:
    users = client.get("/api/admin/users").json()["users"]
    return next(u["id"] for u in users if u["username"] == "tester")


def _grant_auto_mode(client):
    """Owner grants themselves the capability and turns the install switch on."""
    assert client.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200
    uid = _tester_id(client)
    r = client.patch(f"/api/admin/users/{uid}", json={"can_auto_mode": True})
    assert r.status_code == 200 and r.json()["user"]["can_auto_mode"] is True


# --- write flags: lineup + waivers live, trades never auto-execute ---------


def test_lineup_and_waivers_write_trades_never_auto_execute():
    # Lineup (reversible) and waiver add/drop (guarded by a preview + confirm)
    # both write. Trades are never fired autonomously -- only surfaced for the
    # user's one-tap approval.
    assert automode.LINEUP_WRITE_ENABLED is True
    assert automode.WAIVER_WRITE_ENABLED is True
    assert automode.TRADE_AUTO_EXECUTE is False


def test_is_active_needs_all_three():
    assert automode.is_active(install_on=True, capable=True, user_on=True) is True
    assert automode.is_active(install_on=False, capable=True, user_on=True) is False
    assert automode.is_active(install_on=True, capable=False, user_on=True) is False
    assert automode.is_active(install_on=True, capable=True, user_on=False) is False


# --- gating + planning through the API -------------------------------------


def test_auto_mode_is_off_by_default(drafted_league):
    body = drafted_league.get("/api/season/automode").json()
    assert body["gates"] == {"install_enabled": False, "capable": False, "user_enabled": False}
    assert body["plan"]["active"] is False
    assert body["plan"]["reason"]


def test_settings_opt_in_requires_capability(drafted_league):
    # Turning on auto_mode without the granted capability is refused.
    resp = drafted_league.post("/api/season/automode/settings", json={"auto_mode": True})
    assert resp.status_code == 403


def test_lineup_plan_is_computed_and_ready_to_apply_when_active(drafted_league):
    _grant_auto_mode(drafted_league)
    ok = drafted_league.post(
        "/api/season/automode/settings", json={"auto_mode": True, "auto_lineup": True}
    )
    assert ok.status_code == 200 and ok.json()["auto_mode"] is True

    body = drafted_league.get("/api/season/automode").json()
    assert body["plan"]["active"] is True
    # The GET plan itself writes nothing -- the write happens on the Apply action.
    assert body["plan"]["dry_run"] is True
    lineup = body["plan"]["lineup"]
    assert lineup is not None
    # Lineup writing is live: the plan is ready to apply on demand.
    assert lineup["write_enabled"] is True
    assert lineup["status"] == "ready_to_apply"
    # The fixture benches everyone, so the plan wants to start the optimal set.
    assert lineup["start"] or lineup["already_optimal"]

    # And the cycle was logged to the activity trail.
    activity = body["activity"]
    assert any(a["tier"] == "lineup" for a in activity)


def test_admin_switch_and_capability_are_owner_only(drafted_league):
    from app.db import session_scope
    from app.models import User

    # Works as owner.
    assert drafted_league.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200

    with session_scope() as s:
        s.query(User).filter(User.username == "tester").first().role = "client"

    assert drafted_league.post("/api/admin/auto-mode", json={"enabled": False}).status_code == 403
    assert drafted_league.get("/api/admin/auto-mode").status_code == 403


class TestTheLineupReadsTheWeek:
    """Reported from real use: an OUT starter (Brock Bowers) stayed in the lineup
    and Auto Mode logged "Lineup already optimal -- no change" every cycle, while
    the Week screen correctly benched him.

    Cause: Auto Mode optimised on `engine.roster_players`, which carries the
    SEASON projection. An elite player who is OUT this week still ranked first, so
    the "optimal" lineup matched ESPN and the diff was empty. The lineup must be
    scored for the week being set.
    """

    def test_an_out_star_is_benched_not_started(self):
        from app.engine.roster import build_optimal_lineup
        from app.engine.league_shape import LeagueShape
        from app.engine.weekly import WeeklyPlayer

        shape = LeagueShape(team_count=10, dedicated={"TE": 1}, bench_slots=1)

        # A stud TE who is OUT this week, and a modest healthy TE.
        stud = WeeklyPlayer(
            espn_player_id=1, name="Bowers", position="TE",
            week_points=0.0, season_points=300.0, injury_status="OUT", week=1,
        )
        backup = WeeklyPlayer(
            espn_player_id=2, name="Backup TE", position="TE",
            week_points=8.0, season_points=80.0, week=1,
        )

        # Season basis (the bug): the OUT stud wins the TE slot.
        season_lineup = build_optimal_lineup(
            [p.as_roster_player(use_week=False) for p in (stud, backup)], shape
        )
        assert season_lineup.starters[0].player.name == "Bowers"

        # Week basis (the fix): the healthy player starts instead.
        week_lineup = build_optimal_lineup(
            [p.as_roster_player(use_week=True) for p in (stud, backup)], shape
        )
        assert week_lineup.starters[0].player.name == "Backup TE"

    def test_lineup_moves_benches_the_out_player(self):
        from app.engine.league_shape import LeagueShape
        from app.engine.weekly import WeeklyPlayer
        from app.services import automode

        shape = LeagueShape(team_count=10, dedicated={"TE": 1}, bench_slots=1)
        stud = WeeklyPlayer(
            espn_player_id=1, name="Bowers", position="TE",
            week_points=0.0, season_points=300.0, injury_status="OUT", week=1,
        )
        backup = WeeklyPlayer(
            espn_player_id=2, name="Backup TE", position="TE",
            week_points=8.0, season_points=80.0, week=1,
        )
        roster = [p.as_roster_player(use_week=True) for p in (stud, backup)]

        # ESPN currently has the OUT stud starting and the healthy TE benched.
        moves = automode.lineup_moves(roster, shape, {1: "TE", 2: "BE"})
        by_id = {m.espn_player_id: m for m in moves}
        assert by_id[1].to_slot == "BE", "the OUT player must be benched"
        assert by_id[2].to_slot == "TE", "the healthy player must start"
