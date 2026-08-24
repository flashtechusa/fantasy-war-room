"""Writing a waiver / free-agent move to ESPN.

This is the third real write, after the lineup and the trade proposal, and the
first that is only partly reversible: a drop frees a player for anyone to claim,
and a FAAB bid spends real budget. So the caller stages it exactly like the
trade sender -- a preview that shows the exact add/drop, an explicit confirm,
and an audit row -- rather than the one-tap lineup flow.

Two ESPN transaction shapes share this module:

    FREEAGENT -- an immediate add of an unowned player (executes now).
    WAIVER    -- a claim on a player still on waivers, carrying a FAAB
                 bidAmount, processed at the next waiver run.

Both are a transaction whose items ADD the incoming player to my team and
(optionally) DROP one of mine to make room. As with the lineup and trade writes
the exact envelope is reverse-engineered, so `send_waiver` returns ESPN's raw
(redacted) response and the caller surfaces it -- a rejected shape is visible
and correctable, never silent.
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

#: Turned on: add/drop is guarded by a preview + explicit confirm + audit, the
#: same stack that makes the trade sender safe.
WAIVER_WRITE_ENABLED = True

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
    return FANTASY_WRITE_HOST + TRANSACTIONS_PATH.format(season=season, league_id=league_id)


@dataclass
class WaiverPlayer:
    espn_player_id: int
    name: str
    position: str


@dataclass
class WaiverResult:
    ok: bool
    status_code: int
    url: str
    kind: str          # "FREEAGENT" or "WAIVER"
    add: WaiverPlayer
    drop: WaiverPlayer | None
    bid: int
    summary: str
    response: str


def fingerprint(
    *, season: int, league_id: int, team_id: int, add_id: int, drop_id: int | None
) -> str:
    """Stable id for an identical claim, so a double-tap can be refused."""
    import hashlib

    raw = f"{season}:{league_id}:{team_id}:{add_id}:{drop_id or 0}"
    return hashlib.sha256(raw.encode()).hexdigest()


def transaction_type(availability: str | None) -> str:
    """FREEAGENT for an unowned player, WAIVER for one still on the wire."""
    return "WAIVER" if (availability or "").upper() == "WAIVERS" else "FREEAGENT"


def _summary(add: WaiverPlayer, drop: WaiverPlayer | None, kind: str, bid: int) -> str:
    claim = "Claim" if kind == "WAIVER" else "Add"
    text = f"{claim} {add.name} ({add.position})"
    if drop is not None:
        text += f", drop {drop.name} ({drop.position})"
    if kind == "WAIVER" and bid:
        text += f" -- bid {bid} FAAB"
    return text


def build_waiver_body(
    *,
    team_id: int,
    swid: str | None,
    scoring_period_id: int,
    kind: str,
    add: WaiverPlayer,
    drop: WaiverPlayer | None,
    bid: int,
) -> dict:
    """The ESPN transaction for an add (+ optional drop) to my team."""
    items: list[dict] = [
        {"playerId": add.espn_player_id, "type": "ADD", "fromTeamId": 0, "toTeamId": team_id},
    ]
    if drop is not None:
        items.append(
            {"playerId": drop.espn_player_id, "type": "DROP", "fromTeamId": team_id, "toTeamId": 0}
        )
    body = {
        "isLeagueManager": False,
        "teamId": team_id,
        "type": kind,
        "memberId": (swid or "").strip() or None,
        "scoringPeriodId": scoring_period_id,
        "executionType": "EXECUTE",
        "items": items,
    }
    # A FAAB claim carries the bid; an immediate free-agent add does not.
    if kind == "WAIVER":
        body["bidAmount"] = int(bid or 0)
    return body


def send_waiver(
    *,
    season: int,
    league_id: int,
    team_id: int,
    swid: str | None,
    espn_s2: str | None,
    scoring_period_id: int,
    add: WaiverPlayer,
    drop: WaiverPlayer | None,
    availability: str | None,
    bid: int = 0,
    transport: httpx.BaseTransport | None = None,
) -> WaiverResult:
    """POST the add/drop (or waiver claim) to ESPN. Returns the raw response."""
    url = transactions_url(season, league_id)
    kind = transaction_type(availability)
    if not WAIVER_WRITE_ENABLED:
        raise RuntimeError("Waiver writing is disabled (WAIVER_WRITE_ENABLED is False).")
    if not (swid and espn_s2):
        raise RuntimeError("ESPN cookies are missing; connect ESPN before a waiver move.")

    summary = _summary(add, drop, kind, bid)
    body = build_waiver_body(
        team_id=team_id, swid=swid, scoring_period_id=scoring_period_id,
        kind=kind, add=add, drop=drop, bid=bid,
    )
    headers = {**_WRITE_HEADERS, "Cookie": f"espn_s2={espn_s2}; SWID={swid};"}
    try:
        with httpx.Client(timeout=_TIMEOUT, transport=transport, follow_redirects=True) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return WaiverResult(
            False, 0, url, kind, add, drop, bid, summary,
            redact(f"Could not reach ESPN: {exc}"),
        )

    ok = 200 <= resp.status_code < 300
    log.info(
        "Waiver %s to team %s (league %s): HTTP %s", kind, team_id, league_id, resp.status_code
    )
    return WaiverResult(
        ok=ok, status_code=resp.status_code, url=url, kind=kind,
        add=add, drop=drop, bid=bid, summary=summary,
        response=redact((resp.text or "")[:1000]) or f"(empty body, HTTP {resp.status_code})",
    )
