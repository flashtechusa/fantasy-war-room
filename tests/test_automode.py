"""Auto Mode -- staged, dry-run, and gated three ways.

The properties that keep autopilot safe:
- Off by default: no plan runs until the install switch, the per-account
  capability, and the user's own opt-in all line up.
- Dry-run: even fully enabled, Auto Mode plans and logs but writes nothing to
  ESPN (the write flags are False until each payload is captured).
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


# --- the write flags stay off (dry-run) ------------------------------------


def test_writes_are_staged_off():
    assert automode.LINEUP_WRITE_ENABLED is False
    assert automode.WAIVER_WRITE_ENABLED is False
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


def test_lineup_plan_is_computed_but_held_when_active(drafted_league):
    _grant_auto_mode(drafted_league)
    ok = drafted_league.post(
        "/api/season/automode/settings", json={"auto_mode": True, "auto_lineup": True}
    )
    assert ok.status_code == 200 and ok.json()["auto_mode"] is True

    body = drafted_league.get("/api/season/automode").json()
    assert body["plan"]["active"] is True
    assert body["plan"]["dry_run"] is True
    lineup = body["plan"]["lineup"]
    assert lineup is not None
    # Staged: computed, but not written to ESPN.
    assert lineup["write_enabled"] is False
    assert lineup["status"] == "held_pending_capture"
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
