"""Waiver / free-agent writes to ESPN -- guarded like the trade sender.

The properties that keep this safe:
- The transaction shape is built purely: ADD the incoming player, optionally
  DROP one of mine; a FAAB claim carries a bidAmount, an immediate add does not.
- FREEAGENT vs WAIVER is chosen from the player's availability, not guessed.
- The endpoint is gated on the install-wide Auto Mode switch, the owner-granted
  per-user capability, an explicit confirm, present ESPN cookies, a fresh
  revalidation, and a duplicate check.
- Every attempt is audited without credentials; ESPN's raw response is surfaced.
"""

from __future__ import annotations

import httpx

from app.espn import waiver_write


# --- the pure builders ------------------------------------------------------


def test_transaction_type_from_availability():
    assert waiver_write.transaction_type("WAIVERS") == "WAIVER"
    assert waiver_write.transaction_type("FREEAGENT") == "FREEAGENT"
    assert waiver_write.transaction_type(None) == "FREEAGENT"


def test_fingerprint_is_stable_and_distinguishes_the_add():
    a = waiver_write.fingerprint(season=2026, league_id=1, team_id=3, add_id=10, drop_id=20)
    b = waiver_write.fingerprint(season=2026, league_id=1, team_id=3, add_id=10, drop_id=20)
    c = waiver_write.fingerprint(season=2026, league_id=1, team_id=3, add_id=11, drop_id=20)
    assert a == b and a != c


def test_build_body_free_agent_add_drop_has_no_bid():
    add = waiver_write.WaiverPlayer(10, "New RB", "RB")
    drop = waiver_write.WaiverPlayer(20, "Old WR", "WR")
    body = waiver_write.build_waiver_body(
        team_id=3, swid="{SWID}", scoring_period_id=1,
        kind="FREEAGENT", add=add, drop=drop, bid=0,
    )
    assert body["type"] == "FREEAGENT"
    assert body["executionType"] == "EXECUTE"
    assert body["teamId"] == 3 and body["memberId"] == "{SWID}"
    assert "bidAmount" not in body        # immediate add carries no bid
    add_item = next(i for i in body["items"] if i["playerId"] == 10)
    drop_item = next(i for i in body["items"] if i["playerId"] == 20)
    assert add_item["type"] == "ADD" and add_item["toTeamId"] == 3
    assert drop_item["type"] == "DROP" and drop_item["fromTeamId"] == 3


def test_build_body_waiver_claim_carries_the_bid():
    add = waiver_write.WaiverPlayer(10, "Claimed", "RB")
    body = waiver_write.build_waiver_body(
        team_id=3, swid="{SWID}", scoring_period_id=2,
        kind="WAIVER", add=add, drop=None, bid=17,
    )
    assert body["type"] == "WAIVER"
    assert body["bidAmount"] == 17
    # No drop -> a single ADD item.
    assert len(body["items"]) == 1 and body["items"][0]["type"] == "ADD"


# --- send_waiver over the network (mocked) ----------------------------------


def test_send_waiver_posts_to_write_host_with_cookies_and_returns_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("cookie", "")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "wvr-1", "status": "EXECUTED"})

    result = waiver_write.send_waiver(
        season=2026, league_id=11507, team_id=3,
        swid="{SWID}", espn_s2="s2value", scoring_period_id=1,
        add=waiver_write.WaiverPlayer(10, "New RB", "RB"),
        drop=waiver_write.WaiverPlayer(20, "Old WR", "WR"),
        availability="FREEAGENT", bid=0,
        transport=httpx.MockTransport(handler),
    )
    import json

    assert result.ok is True and result.status_code == 200 and result.kind == "FREEAGENT"
    assert "lm-api-writes.fantasy.espn.com" in seen["url"]
    assert "SWID={SWID}" in seen["cookie"] and "espn_s2=s2value" in seen["cookie"]
    body = json.loads(seen["body"])
    assert body["type"] == "FREEAGENT" and {i["type"] for i in body["items"]} == {"ADD", "DROP"}
    assert "wvr-1" in result.response


def test_send_waiver_redacts_a_swid_echoed_back():
    real = "{0899A4A2-0BBB-467C-9A28-CEBC5032330E}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"rejected for SWID={real}")

    result = waiver_write.send_waiver(
        season=2026, league_id=1, team_id=3, swid=real, espn_s2="x",
        scoring_period_id=1, add=waiver_write.WaiverPlayer(10, "RB", "RB"),
        drop=None, availability="WAIVERS", bid=5,
        transport=httpx.MockTransport(handler),
    )
    assert result.ok is False and result.kind == "WAIVER"
    assert "0899A4A2" not in result.response


# --- the endpoint (guarded, no real network) --------------------------------


def _tester_id(client) -> int:
    users = client.get("/api/admin/users").json()["users"]
    return next(u["id"] for u in users if u["username"] == "tester")


def _grant_auto_mode(client, *, install=True):
    if install:
        assert client.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200
    uid = _tester_id(client)
    r = client.patch(f"/api/admin/users/{uid}", json={"can_auto_mode": True})
    assert r.status_code == 200 and r.json()["user"]["can_auto_mode"] is True


def _set_espn_cookies(swid="{TEST-SWID}", s2="test-s2"):
    from app.db import session_scope
    from app.services import runtime_config

    with session_scope() as s:
        runtime_config.write_overrides(s, {"espn_swid": swid, "espn_s2": s2})


def _free_agent_and_drop(client, availability="FREEAGENT"):
    """A guaranteed-unrostered player (past the drafted set) to add, plus a drop."""
    from app.db import session_scope
    from app.models import League, Player

    mine = client.get("/api/season/roster").json()
    drop_id = mine["players"][0]["espn_player_id"]
    with session_scope() as s:
        league = s.query(League).one()
        pool = (
            s.query(Player)
            .filter(Player.season == league.season, Player.source == league.source)
            .order_by(Player.espn_rank)
            .all()
        )
        fa = pool[len(league.teams) * 14 + 5]   # safely beyond the rostered set
        fa.availability = availability
        add_id = fa.espn_player_id
    return add_id, drop_id


def _patch_send_waiver(monkeypatch, *, ok=True, status_code=200,
                       response='{"id":"wvr-1","status":"EXECUTED"}'):
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return waiver_write.WaiverResult(
            ok=ok, status_code=status_code,
            url="https://lm-api-writes.fantasy.espn.com/x",
            kind=waiver_write.transaction_type(kwargs.get("availability")),
            add=kwargs["add"], drop=kwargs.get("drop"), bid=kwargs.get("bid", 0),
            summary="ok", response=response,
        )

    from app.api import routes_season
    monkeypatch.setattr(routes_season.waiver_write, "send_waiver", fake)
    # The write path refreshes rosters live from ESPN first; in tests that would
    # overwrite the fixture's hand-set roster, so no-op it here.
    monkeypatch.setattr(routes_season, "_refresh_rosters", lambda *a, **k: True)
    return captured


def test_preview_is_forbidden_without_the_capability(drafted_league):
    add_id, drop_id = _free_agent_and_drop(drafted_league)
    resp = drafted_league.post(
        "/api/season/waiver/preview", json={"add_id": add_id, "drop_id": drop_id}
    )
    assert resp.status_code == 403


def test_preview_shows_the_exact_move(drafted_league):
    _grant_auto_mode(drafted_league)
    add_id, drop_id = _free_agent_and_drop(drafted_league)
    resp = drafted_league.post(
        "/api/season/waiver/preview", json={"add_id": add_id, "drop_id": drop_id}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["kind"] == "FREEAGENT"
    assert data["add"]["espn_player_id"] == add_id
    assert data["drop"]["espn_player_id"] == drop_id


def test_apply_is_forbidden_without_the_capability(drafted_league):
    add_id, drop_id = _free_agent_and_drop(drafted_league)
    resp = drafted_league.post(
        "/api/season/waiver/apply", json={"add_id": add_id, "drop_id": drop_id, "confirm": True}
    )
    assert resp.status_code == 403


def test_apply_kill_switch_is_off_by_default(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league, install=False)
    _set_espn_cookies()
    captured = _patch_send_waiver(monkeypatch)
    add_id, drop_id = _free_agent_and_drop(drafted_league)
    resp = drafted_league.post(
        "/api/season/waiver/apply", json={"add_id": add_id, "drop_id": drop_id, "confirm": True}
    )
    assert resp.status_code == 403 and "switched off" in resp.json()["detail"]
    assert not captured


def test_apply_requires_confirm(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league)
    _set_espn_cookies()
    captured = _patch_send_waiver(monkeypatch)
    add_id, drop_id = _free_agent_and_drop(drafted_league)
    resp = drafted_league.post(
        "/api/season/waiver/apply", json={"add_id": add_id, "drop_id": drop_id, "confirm": False}
    )
    assert resp.status_code == 400 and not captured


def test_apply_submits_records_audit_and_blocks_duplicates(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league)
    _set_espn_cookies()
    captured = _patch_send_waiver(monkeypatch)
    add_id, drop_id = _free_agent_and_drop(drafted_league)

    body = {"add_id": add_id, "drop_id": drop_id, "confirm": True}
    resp = drafted_league.post("/api/season/waiver/apply", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True and "wvr-1" in resp.json()["response"]
    assert captured["add"].espn_player_id == add_id

    # An audit row with no credentials.
    from app.db import session_scope
    from app.models import WaiverClaimLog

    with session_scope() as s:
        row = s.query(WaiverClaimLog).order_by(WaiverClaimLog.id.desc()).first()
        assert row.outcome == "submitted" and row.ok is True
        assert row.add_id == add_id and row.fingerprint
        assert "TEST-SWID" not in (row.detail or "") and "test-s2" not in (row.detail or "")

    # The identical claim again is refused as a duplicate.
    again = drafted_league.post("/api/season/waiver/apply", json=body)
    assert again.status_code == 409 and "already submitted" in again.json()["detail"]


def test_apply_revalidates_the_drop_is_mine(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league)
    _set_espn_cookies()
    captured = _patch_send_waiver(monkeypatch)
    add_id, _ = _free_agent_and_drop(drafted_league)
    # Drop a player that is NOT on my roster (another free agent).
    other_fa, _ = _free_agent_and_drop(drafted_league, availability="FREEAGENT")

    resp = drafted_league.post(
        "/api/season/waiver/apply",
        json={"add_id": add_id, "drop_id": other_fa, "confirm": True},
    )
    assert resp.status_code == 409
    assert "no longer on your roster" in resp.json()["detail"]
    assert not captured
