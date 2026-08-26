"""Auto Mode's first real ESPN write: setting your own optimal lineup.

The properties that keep this safe (mirroring the trade sender):
- The move set is built purely: only players whose slot actually changes move.
- The write payload is a ROSTER transaction of LINEUP items with explicit
  from/to lineup-slot ids.
- The endpoint is gated on the install-wide Auto Mode switch, the owner-granted
  per-user capability, an explicit confirm, and present ESPN cookies.
- ESPN's raw response is surfaced (redacted), never swallowed, so a rejected
  envelope is visible and correctable.
- Every attempt -- allowed or refused -- is logged, credential-free.
"""

from __future__ import annotations

import httpx

from app.espn import lineup_write


# --- the pure builders ------------------------------------------------------


def test_slot_id_for_maps_labels_and_falls_back_to_bench():
    assert lineup_write.slot_id_for("QB") == 0
    assert lineup_write.slot_id_for("RB") == 2
    assert lineup_write.slot_id_for("FLEX") == 23
    assert lineup_write.slot_id_for("BE") == 20
    # Unknown label -> bench, never a crash.
    assert lineup_write.slot_id_for("???") == lineup_write.BENCH_SLOT_ID


def test_build_moves_only_emits_players_whose_slot_changes():
    optimal = {1: "RB", 2: "BE", 3: "WR"}   # 1 starts, 2 benched, 3 already right
    current = {1: "BE", 2: "RB", 3: "WR"}   # 1 benched, 2 starting, 3 already right
    names = {1: "New Starter", 2: "Benched Guy", 3: "Unchanged"}

    moves = lineup_write.build_moves(
        optimal_slot_by_id=optimal, current_slot_by_id=current, names=names
    )
    moved = {m.espn_player_id for m in moves}
    assert moved == {1, 2}          # player 3 did not move
    by_id = {m.espn_player_id: m for m in moves}
    assert (by_id[1].from_slot, by_id[1].to_slot) == ("BE", "RB")
    assert (by_id[2].from_slot, by_id[2].to_slot) == ("RB", "BE")
    assert by_id[1].name == "New Starter"


def test_build_moves_treats_missing_current_as_bench():
    # A player ESPN didn't list is assumed benched, so promoting them is a move.
    moves = lineup_write.build_moves(
        optimal_slot_by_id={5: "WR"}, current_slot_by_id={}, names={5: "Waiver Add"}
    )
    assert len(moves) == 1 and moves[0].from_slot == "BE" and moves[0].to_slot == "WR"


def test_build_lineup_body_is_a_roster_transaction_of_lineup_items():
    moves = [
        lineup_write.LineupMove(1, "RB In", "BE", "RB"),
        lineup_write.LineupMove(2, "RB Out", "RB", "BE"),
    ]
    body = lineup_write.build_lineup_body(
        team_id=3, swid="{SWID}", scoring_period_id=1, moves=moves
    )
    assert body["type"] == "ROSTER"
    assert body["teamId"] == 3
    assert body["executionType"] == "EXECUTE"
    assert body["scoringPeriodId"] == 1
    assert body["memberId"] == "{SWID}"
    in_item = next(i for i in body["items"] if i["playerId"] == 1)
    out_item = next(i for i in body["items"] if i["playerId"] == 2)
    assert in_item["type"] == "LINEUP"
    assert (in_item["fromLineupSlotId"], in_item["toLineupSlotId"]) == (20, 2)
    assert (out_item["fromLineupSlotId"], out_item["toLineupSlotId"]) == (2, 20)


# --- set_lineup over the network (mocked) -----------------------------------


def test_set_lineup_posts_to_write_host_with_cookies_and_returns_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("cookie", "")
        seen["platform"] = request.headers.get("x-fantasy-platform", "")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"id": "lineup-1", "status": "OK"})

    result = lineup_write.set_lineup(
        season=2026, league_id=11507, team_id=3,
        swid="{SWID}", espn_s2="s2value", scoring_period_id=1,
        moves=[lineup_write.LineupMove(1, "RB", "BE", "RB")],
        transport=httpx.MockTransport(handler),
    )
    import json

    assert result.ok is True and result.status_code == 200
    assert "lm-api-writes.fantasy.espn.com" in seen["url"]
    assert "SWID={SWID}" in seen["cookie"] and "espn_s2=s2value" in seen["cookie"]
    assert seen["platform"] == "espn-fantasy-web"
    body = json.loads(seen["body"])
    assert body["type"] == "ROSTER" and body["items"][0]["type"] == "LINEUP"
    assert "lineup-1" in result.response


def test_set_lineup_with_no_moves_does_not_hit_the_network():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made for an empty move set")

    result = lineup_write.set_lineup(
        season=2026, league_id=1, team_id=3, swid="{SWID}", espn_s2="x",
        scoring_period_id=1, moves=[], transport=httpx.MockTransport(handler),
    )
    assert result.ok is True and result.status_code == 0
    assert "already optimal" in result.response.lower()


def test_set_lineup_redacts_a_swid_echoed_back_by_espn():
    real = "{0899A4A2-0BBB-467C-9A28-CEBC5032330E}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"rejected for SWID={real}")

    result = lineup_write.set_lineup(
        season=2026, league_id=1, team_id=3, swid=real, espn_s2="x",
        scoring_period_id=1, moves=[lineup_write.LineupMove(1, "RB", "BE", "RB")],
        transport=httpx.MockTransport(handler),
    )
    assert result.ok is False
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


def _patch_set_lineup(monkeypatch, *, ok=True, status_code=200, response='{"id":"lineup-1"}'):
    captured = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return lineup_write.LineupResult(
            ok=ok, status_code=status_code,
            url="https://lm-api-writes.fantasy.espn.com/x",
            moves=kwargs.get("moves", []), response=response,
        )

    from app.api import routes_season
    monkeypatch.setattr(routes_season.lineup_write, "set_lineup", fake)
    # The write path refreshes rosters live from ESPN first; in tests that would
    # overwrite the fixture's hand-set roster, so no-op it here. Its own behavior
    # (that it is called before diffing) is covered by test_apply_refreshes_first.
    monkeypatch.setattr(routes_season, "_refresh_rosters", lambda *a, **k: True)
    return captured


def test_apply_is_forbidden_without_the_capability(drafted_league):
    resp = drafted_league.post("/api/season/lineup/apply", json={"confirm": True})
    assert resp.status_code == 403


def test_apply_kill_switch_is_off_by_default(drafted_league, monkeypatch):
    # Capability granted, cookies present, confirmed -- but the install switch is
    # off, so the write is refused before anything is posted.
    _grant_auto_mode(drafted_league, install=False)
    _set_espn_cookies()
    captured = _patch_set_lineup(monkeypatch)
    resp = drafted_league.post("/api/season/lineup/apply", json={"confirm": True})
    assert resp.status_code == 403
    assert "switched off" in resp.json()["detail"]
    assert not captured  # never reached ESPN


def test_apply_requires_explicit_confirm(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league)
    _set_espn_cookies()
    captured = _patch_set_lineup(monkeypatch)
    resp = drafted_league.post("/api/season/lineup/apply", json={"confirm": False})
    assert resp.status_code == 400
    assert not captured


def test_apply_requires_espn_cookies(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league)
    _patch_set_lineup(monkeypatch)  # cookies deliberately not set
    resp = drafted_league.post("/api/season/lineup/apply", json={"confirm": True})
    assert resp.status_code == 409
    assert "Connect ESPN" in resp.json()["detail"]


def test_apply_writes_the_lineup_and_records_activity(drafted_league, monkeypatch):
    _grant_auto_mode(drafted_league)
    _set_espn_cookies()
    captured = _patch_set_lineup(monkeypatch)

    resp = drafted_league.post("/api/season/lineup/apply", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True and "lineup-1" in data["response"]
    # The fixture benches everyone, so a real set of moves was computed and sent.
    assert captured and captured["team_id"]
    assert captured["moves"], "expected lineup moves for an all-benched roster"

    # An activity row was written -- with no credentials in it.
    from app.db import session_scope
    from app.models import AutoModeRun

    with session_scope() as s:
        row = s.query(AutoModeRun).filter(AutoModeRun.tier == "lineup").order_by(
            AutoModeRun.id.desc()
        ).first()
        assert row is not None and row.status == "applied"
        assert "TEST-SWID" not in (row.summary or "") and "test-s2" not in (row.summary or "")


def test_apply_refreshes_rosters_before_diffing(drafted_league, monkeypatch):
    # The staleness fix: the lineup diff must be computed against ESPN's real
    # current roster, so a refresh runs before set_lineup. (Without it, a move
    # ESPN already applied is re-sent and refused as TRAN_ROSTER_SAME_SLOT.)
    _grant_auto_mode(drafted_league)
    _set_espn_cookies()
    from app.api import routes_season

    order = []
    monkeypatch.setattr(routes_season.lineup_write, "set_lineup", lambda **k: (
        order.append("write"),
        routes_season.lineup_write.LineupResult(True, 200, "u", k.get("moves", []), "{}"),
    )[1])
    monkeypatch.setattr(routes_season, "_refresh_rosters",
                        lambda *a, **k: order.append("refresh") or True)

    resp = drafted_league.post("/api/season/lineup/apply", json={"confirm": True})
    assert resp.status_code == 200, resp.text
    # Refreshed first, then wrote.
    assert order and order[0] == "refresh" and "write" in order
