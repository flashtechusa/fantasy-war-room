"""Exact ESPN transaction payloads used by Auto Mode.

No test in this file touches ESPN.  The undocumented write contract is locked at
the pure-builder/MockTransport boundary so a refactor cannot silently change a
real roster mutation.
"""

from __future__ import annotations

import json

import httpx

from app.espn.transaction_write import (
    WRITE_HEADERS,
    build_freeagent_body,
    build_lineup_body,
    build_waiver_body,
    send_transaction,
    transactions_url,
)

SWID = "{11111111-2222-3333-4444-555555555555}"


def test_lineup_payload_matches_espn_transaction_shape():
    body = build_lineup_body(
        team_id=7,
        swid=SWID,
        scoring_period_id=3,
        moves=[(101, 20, 2), (202, 2, 20)],
    )
    assert body == {
        "isLeagueManager": False,
        "teamId": 7,
        "type": "ROSTER",
        "memberId": SWID,
        "scoringPeriodId": 3,
        "executionType": "EXECUTE",
        "items": [
            {
                "playerId": 101,
                "type": "LINEUP",
                "fromLineupSlotId": 20,
                "toLineupSlotId": 2,
            },
            {
                "playerId": 202,
                "type": "LINEUP",
                "fromLineupSlotId": 2,
                "toLineupSlotId": 20,
            },
        ],
    }
    assert all("fromTeamId" not in item and "toTeamId" not in item for item in body["items"])


def test_freeagent_add_drop_payload():
    body = build_freeagent_body(
        team_id=7,
        swid=SWID,
        scoring_period_id=4,
        add_player_id=303,
        drop_player_id=404,
    )
    assert body["type"] == "FREEAGENT"
    assert body["executionType"] == "EXECUTE"
    assert body["items"] == [
        {"playerId": 303, "type": "ADD", "toTeamId": 7},
        {"playerId": 404, "type": "DROP", "fromTeamId": 7},
    ]


def test_waiver_payload_includes_faab_bid():
    body = build_waiver_body(
        team_id=12,
        swid=SWID,
        scoring_period_id=5,
        add_player_id=505,
        drop_player_id=606,
        bid_amount=17,
    )
    assert body["type"] == "WAIVER"
    assert body["bidAmount"] == 17
    assert body["items"] == [
        {"playerId": 505, "type": "ADD", "toTeamId": 12},
        {"playerId": 606, "type": "DROP", "fromTeamId": 12},
    ]


def test_write_host_and_current_web_headers():
    assert transactions_url(2026, 11507) == (
        "https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/"
        "seasons/2026/segments/0/leagues/11507/transactions/"
    )
    assert WRITE_HEADERS["X-Fantasy-Source"] == "kona"
    assert WRITE_HEADERS["X-Fantasy-Platform"] == "espn-fantasy-web"


def test_send_posts_once_and_uses_cookie_without_returning_it():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["cookie"] = request.headers.get("cookie")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "EXECUTED"})

    body = build_freeagent_body(
        team_id=7, swid=SWID, scoring_period_id=1, add_player_id=99
    )
    result = send_transaction(
        season=2026,
        league_id=11507,
        swid=SWID,
        espn_s2="very-secret-session-cookie",
        body=body,
        transport=httpx.MockTransport(handler),
    )

    assert result.ok is True
    assert result.status_code == 200
    assert seen["method"] == "POST"
    assert seen["body"] == body
    assert "espn_s2=very-secret-session-cookie" in seen["cookie"]
    assert "very-secret-session-cookie" not in result.response
    assert SWID not in result.response
