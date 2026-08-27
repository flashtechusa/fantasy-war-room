"""The autonomous Auto Mode cycle -- the scheduler that runs a team on its own.

Properties that keep an unattended run safe:
- Off by default: the cycle writes nothing unless the install switch, the
  per-account grant, and the user's own opt-in all line up.
- It performs the same lineup write the Auto tab does -- and nothing more:
  waivers are held (AUTO_WAIVER_EXECUTE is False) and trades are never fired.
- Every action is logged, credential-free, and one user's failure never aborts
  the cycle for the others.
- The on-demand triggers (user "Run now", owner "run install cycle") are gated
  the same way and just call the cycle.
"""

from __future__ import annotations

import app.espn.lineup_write as lineup_write
from app.services import automode_runner


def _tester_id(client) -> int:
    users = client.get("/api/admin/users").json()["users"]
    return next(u["id"] for u in users if u["username"] == "tester")


def _enable_everything(client):
    """Install switch on, capability granted, user opted in with the lineup tier."""
    assert client.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200
    uid = _tester_id(client)
    assert client.patch(f"/api/admin/users/{uid}", json={"can_auto_mode": True}).status_code == 200
    r = client.post("/api/season/automode/settings",
                    json={"auto_mode": True, "auto_lineup": True})
    assert r.status_code == 200 and r.json()["auto_mode"] is True


def _set_espn_cookies(swid="{TEST-SWID}", s2="test-s2"):
    from app.db import session_scope
    from app.services import runtime_config

    with session_scope() as s:
        runtime_config.write_overrides(s, {"espn_swid": swid, "espn_s2": s2})


def _patch_cycle(monkeypatch, *, ok=True, status_code=200):
    """No-op the live refresh and week lookup; capture the lineup write."""
    captured = {}

    def fake_set(**kwargs):
        captured.update(kwargs)
        return lineup_write.LineupResult(
            ok=ok, status_code=status_code, url="u",
            moves=kwargs.get("moves", []), response="{}",
        )

    monkeypatch.setattr(lineup_write, "set_lineup", fake_set)
    monkeypatch.setattr(automode_runner, "refresh_rosters", lambda *a, **k: True)
    monkeypatch.setattr(automode_runner, "_week", lambda *a, **k: 1)
    return captured


# --- staging flags ----------------------------------------------------------


def test_only_lineup_executes_in_the_cycle():
    assert automode_runner.AUTO_LINEUP_EXECUTE is True
    assert automode_runner.AUTO_WAIVER_EXECUTE is False


# --- the cycle service ------------------------------------------------------


def test_cycle_is_a_noop_when_the_install_switch_is_off(drafted_league, monkeypatch):
    _enable_everything(drafted_league)
    # Turn the install switch back off; nothing should run.
    assert drafted_league.post("/api/admin/auto-mode", json={"enabled": False}).status_code == 200
    captured = _patch_cycle(monkeypatch)

    from app.db import session_scope

    with session_scope() as s:
        result = automode_runner.run_cycle(s)
    assert result["install_enabled"] is False
    assert not captured  # never wrote to ESPN


def test_cycle_sets_the_lineup_for_an_enabled_user(drafted_league, monkeypatch):
    _enable_everything(drafted_league)
    _set_espn_cookies()
    captured = _patch_cycle(monkeypatch)

    from app.db import session_scope

    with session_scope() as s:
        result = automode_runner.run_cycle(s)
    assert result["install_enabled"] is True
    ran = result["ran"]
    assert any(r["user"] == "tester" for r in ran)
    tiers = [a["tier"] for r in ran for a in r["actions"]]
    assert "lineup" in tiers
    # The fixture benches everyone, so a real lineup write was attempted.
    assert captured and captured.get("team_id")


def test_cycle_holds_waivers(drafted_league, monkeypatch):
    _enable_everything(drafted_league)
    _set_espn_cookies()
    _patch_cycle(monkeypatch)
    # Opt the waivers tier in too.
    assert drafted_league.post("/api/season/automode/settings",
                               json={"auto_waivers": True}).status_code == 200

    from app.db import session_scope

    with session_scope() as s:
        result = automode_runner.run_cycle(s)
    actions = [a for r in result["ran"] if r["user"] == "tester" for a in r["actions"]]
    waiver = next((a for a in actions if a["tier"] == "waivers"), None)
    assert waiver is not None and waiver["status"] == "held"


def test_cycle_skips_a_user_with_no_cookies(drafted_league, monkeypatch):
    _enable_everything(drafted_league)  # capability + opt-in, but no cookies set
    captured = _patch_cycle(monkeypatch)

    from app.db import session_scope

    with session_scope() as s:
        result = automode_runner.run_cycle(s)
    actions = [a for r in result["ran"] if r["user"] == "tester" for a in r["actions"]]
    assert actions and actions[0]["status"] == "skipped"
    assert not captured  # nothing sent


# --- on-demand triggers -----------------------------------------------------


def test_run_now_is_forbidden_without_capability(drafted_league):
    assert drafted_league.post("/api/season/automode/run").status_code == 403


def test_run_now_reports_when_not_opted_in(drafted_league, monkeypatch):
    # Capability granted + install on, but the user has not opted in.
    assert drafted_league.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200
    uid = _tester_id(drafted_league)
    drafted_league.patch(f"/api/admin/users/{uid}", json={"can_auto_mode": True})
    _patch_cycle(monkeypatch)

    resp = drafted_league.post("/api/season/automode/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ran"] is False and "opt-in" in body["reason"].lower()


def test_run_now_runs_my_cycle(drafted_league, monkeypatch):
    _enable_everything(drafted_league)
    _set_espn_cookies()
    captured = _patch_cycle(monkeypatch)

    resp = drafted_league.post("/api/season/automode/run")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ran"] is True
    assert any(a["tier"] == "lineup" for a in body["actions"])
    assert captured  # the lineup write was attempted


def test_admin_run_is_owner_only(drafted_league):
    from app.db import session_scope
    from app.models import User

    with session_scope() as s:
        s.query(User).filter(User.username == "tester").first().role = "client"
    assert drafted_league.post("/api/admin/auto-mode/run").status_code == 403


# --- the scheduler entry point ----------------------------------------------


def test_cli_prints_a_per_user_summary(monkeypatch, capsys):
    import contextlib

    import app.automode_cycle as cli

    monkeypatch.setattr(cli, "init_db", lambda: None)

    @contextlib.contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr(cli, "session_scope", fake_scope)
    monkeypatch.setattr(cli, "run_cycle", lambda session, only_user_id=None: {
        "install_enabled": True,
        "ran": [{"user": "tester", "actions": [{"tier": "lineup", "status": "applied"}]}],
    })
    code = cli.main([])
    out = capsys.readouterr().out
    assert code == 0 and "tester" in out and "lineup=applied" in out


def test_cli_says_nothing_to_do_when_switch_off(monkeypatch, capsys):
    import contextlib

    import app.automode_cycle as cli

    monkeypatch.setattr(cli, "init_db", lambda: None)

    @contextlib.contextmanager
    def fake_scope():
        yield object()

    monkeypatch.setattr(cli, "session_scope", fake_scope)
    monkeypatch.setattr(cli, "run_cycle",
                        lambda session, only_user_id=None: {"install_enabled": False, "ran": []})
    assert cli.main([]) == 0
    assert "nothing to do" in capsys.readouterr().out.lower()
