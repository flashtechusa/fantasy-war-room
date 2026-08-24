"""ESPN free-agent and waiver writes for Auto Mode.

ESPN's write API is undocumented.  These payloads use the same authenticated
transactions endpoint and current web-client headers as the already-live trade
and lineup writers.  Builders are pure so tests can lock the exact request
without touching a real league.  A mutation is posted exactly once; it is never
retried automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .redaction import redact

log = logging.getLogger(__name__)

FANTASY_WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"
TRANSACTIONS_PATH = (
    "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}/transactions/"
)
WIRE_WRITE_ENABLED = True

_WRITE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-Fantasy-Source": "kona",
    "X-Fantasy-Platform": "espn-fantasy-web",
    "Origin": "https://fantasy.espn.com",
    "Referer": "https://fantasy.espn.com/",
}
_TIMEOUT = 20.0


def transactions_url(season: int, league_id: int) -> str:
    return FANTASY_WRITE_HOST + TRANSACTIONS_PATH.format(
        season=season, league_id=league_id
    )


def _base_body(
    *,
    team_id: int,
    swid: str | None,
    scoring_period_id: int,
    transaction_type: str,
    items: list[dict],
    **extra,
) -> dict:
    body = {
        "isLeagueManager": False,
        "teamId": int(team_id),
        "type": transaction_type,
        "memberId": (swid or "").strip() or None,
        "scoringPeriodId": int(scoring_period_id),
        "executionType": "EXECUTE",
        "items": items,
    }
    body.update(extra)
    return body


def build_freeagent_body(
    *,
    team_id: int,
    swid: str | None,
    scoring_period_id: int,
    add_player_id: int | None = None,
    drop_player_id: int | None = None,
) -> dict:
    """Immediate free-agent ADD/DROP transaction."""
    items: list[dict] = []
    if add_player_id is not None:
        items.append(
            {"playerId": int(add_player_id), "type": "ADD", "toTeamId": int(team_id)}
        )
    if drop_player_id is not None:
        items.append(
            {
                "playerId": int(drop_player_id),
                "type": "DROP",
                "fromTeamId": int(team_id),
            }
        )
    return _base_body(
        team_id=team_id,
        swid=swid,
        scoring_period_id=scoring_period_id,
        transaction_type="FREEAGENT",
        items=items,
    )


def build_waiver_body(
    *,
    team_id: int,
    swid: str | None,
    scoring_period_id: int,
    add_player_id: int,
    drop_player_id: int | None = None,
    bid_amount: int | None = None,
) -> dict:
    """Pending waiver claim. ``bidAmount`` is used by FAAB leagues."""
    items: list[dict] = [
        {"playerId": int(add_player_id), "type": "ADD", "toTeamId": int(team_id)}
    ]
    if drop_player_id is not None:
        items.append(
            {
                "playerId": int(drop_player_id),
                "type": "DROP",
                "fromTeamId": int(team_id),
            }
        )
    return _base_body(
        team_id=team_id,
        swid=swid,
        scoring_period_id=scoring_period_id,
        transaction_type="WAIVER",
        items=items,
        bidAmount=None if bid_amount is None else int(bid_amount),
    )


@dataclass
class WireResult:
    ok: bool
    status_code: int
    url: str
    response: str


def send_wire_transaction(
    *,
    season: int,
    league_id: int,
    swid: str | None,
    espn_s2: str | None,
    body: dict,
    transport: httpx.BaseTransport | None = None,
) -> WireResult:
    """POST one free-agent/waiver mutation exactly once."""
    if not WIRE_WRITE_ENABLED:
        raise RuntimeError("Wire writing is disabled (WIRE_WRITE_ENABLED is False).")
    if not (swid and espn_s2):
        raise RuntimeError("ESPN cookies are missing; reconnect ESPN before Auto Mode writes.")
    if not body.get("items"):
        raise RuntimeError("Refusing an empty ESPN wire transaction.")

    url = transactions_url(season, league_id)
    headers = {
        **_WRITE_HEADERS,
        "Cookie": f"espn_s2={espn_s2}; SWID={swid};",
    }
    try:
        with httpx.Client(
            timeout=_TIMEOUT, transport=transport, follow_redirects=True
        ) as client:
            response = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return WireResult(
            ok=False,
            status_code=0,
            url=url,
            response=redact(f"Could not reach ESPN: {exc}"),
        )

    text = redact((response.text or "")[:1000])
    log.info(
        "ESPN %s write to team %s (league %s): HTTP %s",
        body.get("type"), body.get("teamId"), league_id, response.status_code,
    )
    return WireResult(
        ok=200 <= response.status_code < 300,
        status_code=response.status_code,
        url=url,
        response=text or f"(empty body, HTTP {response.status_code})",
    )
