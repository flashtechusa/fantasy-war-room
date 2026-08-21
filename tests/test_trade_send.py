"""Send-to-ESPN: an admin-granted capability, a masked preview, and a guarded send.

The properties that keep this safe:
- The write payload is built purely, with unambiguous per-player direction.
- The preview never sends and never shows a live SWID (it is masked).
- Both preview and send are gated on the owner-granted `can_send_trades`.
- The send additionally requires an explicit confirm, resolves the trade exactly
  like the preview, and surfaces ESPN's raw response rather than swallowing it.
- Only the owner can grant the capability.
"""

from __future__ import annotations

import httpx

from app.espn import trade_write
from app.espn.redaction import SWID_PLACEHOLDER


# --- the payload builder (pure) --------------------------------------------


def test_build_body_matches_espns_trade_envelope():
    give = [trade_write.TradePlayer(espn_player_id=1, name="My RB", position="RB")]
    receive = [trade_write.TradePlayer(espn_player_id=99, name="Their WR", position="WR")]
    body = trade_write.build_trade_body(
        my_team_id=3, their_team_id=7, swid="{SWID}", give=give, receive=receive
    )
    assert body["type"] == "TRADE_PROPOSAL"
    assert body["teamId"] == 3
    assert body["executionType"] == "EXECUTE"
    # The counterparty is expressed only through item from/to -- not these fields.
    assert "proposingTeamId" not in body and "acceptingTeamId" not in body
    # Proposal-specific fields ESPN's web client includes.
    assert body["comment"] == ""
    assert body["expirationDate"].endswith("Z") and "T" in body["expirationDate"]
    # The player I give moves me -> them; the one I receive moves them -> me.
    give_item = next(i for i in body["items"] if i["playerId"] == 1)
    recv_item = next(i for i in body["items"] if i["playerId"] == 99)
    assert give_item["type"] == "TRADE"
    assert (give_item["fromTeamId"], give_item["toTeamId"]) == (3, 7)
    assert (recv_item["fromTeamId"], recv_item["toTeamId"]) == (7, 3)


def test_preview_masks_the_swid_and_sends_nothing():
    real_swid = "{0899A4A2-0BBB-467C-9A28-CEBC5032330E}"
    p = trade_write.preview_trade(
        season=2026, league_id=11507,
        my_team_id=3, my_team_name="Me",
        their_team_id=7, their_team_name="Them",
        swid=real_swid,
        give=[trade_write.TradePlayer(1, "My RB", "RB")],
        receive=[trade_write.TradePlayer(99, "Their WR", "WR")],
    )
    assert p.sent is False
    assert p.url.startswith("https://lm-api-writes.fantasy.espn.com/")
    # The live SWID is never shown in the preview -- it is masked.
    assert p.body["memberId"] == SWID_PLACEHOLDER
    assert real_swid not in str(p.body)


def test_send_posts_to_the_write_host_with_cookies_and_returns_the_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("cookie", "")
        seen["platform"] = request.headers.get("x-fantasy-platform", "")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "txn-123", "status": "PENDING"})

    result = trade_write.send_trade(
        season=2026, league_id=11507,
        my_team_id=3, my_team_name="Me",
        their_team_id=7, their_team_name="Them",
        swid="{SWID}", espn_s2="s2cookievalue",
        give=[trade_write.TradePlayer(1, "My RB", "RB")],
        receive=[trade_write.TradePlayer(99, "Their WR", "WR")],
        transport=httpx.MockTransport(handler),
    )
    import json

    assert result.ok is True
    assert result.status_code == 200
    assert "lm-api-writes.fantasy.espn.com" in seen["url"]
    # Cookies were attached; the real send carries the true SWID (not masked).
    assert "SWID={SWID}" in seen["cookie"] and "espn_s2=s2cookievalue" in seen["cookie"]
    # Current ESPN web-client platform header (not the older kona-PROD form).
    assert seen["platform"] == "espn-fantasy-web"
    # The real send carries the true member id (not the masked placeholder).
    body = json.loads(seen["body"])
    assert body["memberId"] == "{SWID}"
    assert body["executionType"] == "EXECUTE" and "proposingTeamId" not in body
    assert "txn-123" in result.response


def test_send_redacts_a_swid_echoed_back_by_espn():
    def handler(request: httpx.Request) -> httpx.Response:
        # ESPN error responses sometimes echo the request, cookies included.
        return httpx.Response(400, text='rejected for SWID={0899A4A2-0BBB-467C-9A28-CEBC5032330E}')

    result = trade_write.send_trade(
        season=2026, league_id=11507,
        my_team_id=3, my_team_name="Me", their_team_id=7, their_team_name="Them",
        swid="{0899A4A2-0BBB-467C-9A28-CEBC5032330E}", espn_s2="x",
        give=[trade_write.TradePlayer(1, "RB", "RB")], receive=[],
        transport=httpx.MockTransport(handler),
    )
    assert result.ok is False
    assert "0899A4A2" not in result.response  # redacted


# --- the capability gate + endpoint ----------------------------------------


def _grant_trade_send(username: str, enabled: bool = True):
    from app.db import session_scope
    from app.models import User

    with session_scope() as s:
        u = s.query(User).filter(User.username == username).first()
        u.can_send_trades = enabled


def test_preview_is_forbidden_without_the_capability(drafted_league):
    body = {"their_team_id": 2, "give_ids": [], "receive_ids": []}
    resp = drafted_league.post("/api/season/trade/preview", json=body)
    assert resp.status_code == 403


def test_preview_works_once_granted(drafted_league):
    _grant_trade_send("tester", True)
    # Pick a real other team and one of its players + one of mine.
    status = drafted_league.get("/api/espn/status").json()
    my_team_id = status.get("my_team_id")
    teams = drafted_league.get("/api/team/league").json()["teams"]
    theirs = next(t for t in teams if t["espn_team_id"] != my_team_id)
    their_roster = drafted_league.get(
        f"/api/season/roster?team_id={theirs['espn_team_id']}"
    ).json()
    mine = drafted_league.get("/api/season/roster").json()
    give_id = mine["players"][0]["espn_player_id"]
    recv_id = their_roster["players"][0]["espn_player_id"]

    resp = drafted_league.post(
        "/api/season/trade/preview",
        json={
            "their_team_id": theirs["espn_team_id"],
            "give_ids": [give_id],
            "receive_ids": [recv_id],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is False
    assert data["request"]["method"] == "POST"
    # The SWID is masked in the preview response.
    assert data["request"]["body"]["memberId"] == SWID_PLACEHOLDER
    # Direction is preserved end to end.
    items = data["request"]["body"]["items"]
    assert any(i["playerId"] == give_id and i["toTeamId"] == theirs["espn_team_id"] for i in items)
    assert any(i["playerId"] == recv_id and i["fromTeamId"] == theirs["espn_team_id"] for i in items)


def test_only_owner_can_grant_capability(drafted_league):
    # Create a non-owner account, then try to grant from it -> forbidden.
    from app.db import session_scope
    from app.models import User

    made = drafted_league.post(
        "/api/admin/users", json={"username": "client1", "role": "client"}
    )
    assert made.status_code == 201, made.text
    with session_scope() as s:
        client = s.query(User).filter(User.username == "client1").first()
        client_id = client.id
        # Demote the test user so it is no longer owner.
        me = s.query(User).filter(User.username == "tester").first()
        me.role = "client"

    resp = drafted_league.patch(
        f"/api/admin/users/{client_id}", json={"can_send_trades": True}
    )
    assert resp.status_code == 403


# --- the send endpoint (guarded, no real network) --------------------------


def _a_valid_trade(client):
    status = client.get("/api/espn/status").json()
    my_team_id = status.get("my_team_id")
    teams = client.get("/api/team/league").json()["teams"]
    theirs = next(t for t in teams if t["espn_team_id"] != my_team_id)
    their_roster = client.get(f"/api/season/roster?team_id={theirs['espn_team_id']}").json()
    mine = client.get("/api/season/roster").json()
    return {
        "their_team_id": theirs["espn_team_id"],
        "give_ids": [mine["players"][0]["espn_player_id"]],
        "receive_ids": [their_roster["players"][0]["espn_player_id"]],
    }


def _enable_global_sending(client, enabled: bool = True):
    resp = client.post("/api/admin/trade-sending", json={"enabled": enabled})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is enabled


def _set_espn_cookies(swid="{TEST-SWID}", s2="test-s2"):
    from app.db import session_scope
    from app.services import runtime_config

    with session_scope() as s:
        runtime_config.write_overrides(s, {"espn_swid": swid, "espn_s2": s2})


def _patch_send(monkeypatch, *, ok=True, status_code=200, response='{"id":"txn-1"}'):
    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return trade_write.SendResult(
            ok=ok, status_code=status_code,
            url="https://lm-api-writes.fantasy.espn.com/x",
            summary="ok", response=response,
        )

    from app.api import routes_season
    monkeypatch.setattr(routes_season.trade_write, "send_trade", fake_send)
    return captured


def test_send_is_forbidden_without_the_capability(drafted_league):
    body = {**_a_valid_trade(drafted_league), "confirm": True}
    assert drafted_league.post("/api/season/trade/send", json=body).status_code == 403


def test_global_kill_switch_is_off_by_default(drafted_league, monkeypatch):
    # Capability granted, cookies present, confirmed -- but the install switch is
    # off, so the send is refused before anything is posted.
    _grant_trade_send("tester", True)
    _set_espn_cookies()
    _patch_send(monkeypatch)
    body = {**_a_valid_trade(drafted_league), "confirm": True}
    resp = drafted_league.post("/api/season/trade/send", json=body)
    assert resp.status_code == 403
    assert "switched off" in resp.json()["detail"]


def test_send_requires_explicit_confirm(drafted_league):
    _grant_trade_send("tester", True)
    _enable_global_sending(drafted_league)
    body = {**_a_valid_trade(drafted_league), "confirm": False}
    resp = drafted_league.post("/api/season/trade/send", json=body)
    assert resp.status_code == 400


def test_send_posts_records_audit_and_blocks_duplicates(drafted_league, monkeypatch):
    _grant_trade_send("tester", True)
    _enable_global_sending(drafted_league)
    _set_espn_cookies()
    captured = _patch_send(monkeypatch)

    body = {**_a_valid_trade(drafted_league), "confirm": True}
    resp = drafted_league.post("/api/season/trade/send", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True and "txn-1" in resp.json()["response"]
    assert captured["give"] and captured["their_team_id"]

    # An audit row was written -- with no credentials in it.
    from app.db import session_scope
    from app.models import TradeSendLog

    with session_scope() as s:
        row = s.query(TradeSendLog).order_by(TradeSendLog.id.desc()).first()
        assert row.outcome == "sent" and row.ok is True
        assert row.username == "tester" and row.fingerprint
        blob = f"{row.detail} {row.give_ids} {row.receive_ids}"
        assert "TEST-SWID" not in blob and "test-s2" not in blob

    # The identical proposal a second time is refused as a duplicate.
    again = drafted_league.post("/api/season/trade/send", json=body)
    assert again.status_code == 409
    assert "already sent" in again.json()["detail"]


def test_send_revalidates_rosters(drafted_league, monkeypatch):
    _grant_trade_send("tester", True)
    _enable_global_sending(drafted_league)
    _set_espn_cookies()
    _patch_send(monkeypatch)

    valid = _a_valid_trade(drafted_league)
    # Give a player that is NOT on my roster (take one from the other team).
    their_roster = drafted_league.get(
        f"/api/season/roster?team_id={valid['their_team_id']}"
    ).json()
    not_mine = their_roster["players"][1]["espn_player_id"]
    body = {**valid, "give_ids": [not_mine], "confirm": True}
    resp = drafted_league.post("/api/season/trade/send", json=body)
    assert resp.status_code == 409
    assert "no longer on your roster" in resp.json()["detail"]


def test_kill_switch_is_owner_only(drafted_league):
    from app.db import session_scope
    from app.models import User

    with session_scope() as s:
        s.query(User).filter(User.username == "tester").first().role = "client"
    resp = drafted_league.post("/api/admin/trade-sending", json={"enabled": True})
    assert resp.status_code == 403
