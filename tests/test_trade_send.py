"""Send-to-ESPN, Stage 1: an admin-granted capability and a preview that sends nothing.

The properties that keep this safe:
- The write payload is built purely, with unambiguous per-player direction.
- Live sending is OFF (`SEND_ENABLED` is False) and the preview never sends.
- The preview endpoint is gated: forbidden unless the owner granted the account
  `can_send_trades`.
- Only the owner can grant the capability.
"""

from __future__ import annotations

from app.espn import trade_write


# --- the payload builder (pure) --------------------------------------------


def test_build_body_has_unambiguous_direction():
    give = [trade_write.TradePlayer(espn_player_id=1, name="My RB", position="RB")]
    receive = [trade_write.TradePlayer(espn_player_id=99, name="Their WR", position="WR")]
    body = trade_write.build_trade_body(
        my_team_id=3, their_team_id=7, swid="{SWID}", give=give, receive=receive
    )
    assert body["type"] == "TRADE_PROPOSAL"
    assert body["proposingTeamId"] == 3
    assert body["acceptingTeamId"] == 7
    # The player I give moves me -> them; the one I receive moves them -> me.
    give_item = next(i for i in body["items"] if i["playerId"] == 1)
    recv_item = next(i for i in body["items"] if i["playerId"] == 99)
    assert (give_item["fromTeamId"], give_item["toTeamId"]) == (3, 7)
    assert (recv_item["fromTeamId"], recv_item["toTeamId"]) == (7, 3)


def test_live_sending_is_disabled():
    # The master switch stays off until the payload is validated live.
    assert trade_write.SEND_ENABLED is False


def test_preview_sends_nothing():
    p = trade_write.preview_trade(
        season=2026, league_id=11507,
        my_team_id=3, my_team_name="Me",
        their_team_id=7, their_team_name="Them",
        swid="{SWID}",
        give=[trade_write.TradePlayer(1, "My RB", "RB")],
        receive=[trade_write.TradePlayer(99, "Their WR", "WR")],
    )
    assert p.sent is False
    assert p.url.startswith("https://lm-api-writes.fantasy.espn.com/")
    assert "Preview only" in p.note


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
    assert data["send_enabled"] is False
    assert data["request"]["method"] == "POST"
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
