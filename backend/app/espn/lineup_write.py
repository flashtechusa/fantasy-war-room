"""Writing a lineup to ESPN -- the safest real write, so Auto Mode can act.

Setting your own lineup is the ideal first autonomous write: it only ever
touches your own team and it is fully reversible (move a player back). It uses
the same authenticated writes host, headers, and current-scoring-period rule we
proved with the trade sender.

A lineup change is a ROSTER transaction whose items each MOVE one player from
their current lineup slot to a new one (bench <-> a starting slot). ESPN
validates the whole set together. As with trades, the exact envelope is
reverse-engineered, so `set_lineup` returns ESPN's raw (redacted) response and
the caller surfaces it -- a rejected shape is visible and correctable, never
silent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .constants import SLOT_ID_TO_LABEL
from .redaction import redact

log = logging.getLogger(__name__)

FANTASY_WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"
TRANSACTIONS_PATH = (
    "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}/transactions/"
)

#: Turned on: setting your own lineup is reversible and low-risk, so this is the
#: first write Auto Mode is allowed to actually perform (still behind the install
#: switch, the per-user capability, and the user's opt-in).
LINEUP_WRITE_ENABLED = True

#: Label -> ESPN lineupSlotId, the inverse of the read-side slot map.
LABEL_TO_SLOT_ID = {label: sid for sid, label in SLOT_ID_TO_LABEL.items()}
BENCH_SLOT_ID = 20

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


def slot_id_for(label: str) -> int:
    """ESPN lineupSlotId for one of our slot labels; bench for anything unknown."""
    return LABEL_TO_SLOT_ID.get((label or "").upper(), BENCH_SLOT_ID)


@dataclass
class LineupMove:
    espn_player_id: int
    name: str
    from_slot: str
    to_slot: str


@dataclass
class LineupResult:
    ok: bool
    status_code: int
    url: str
    moves: list[LineupMove]
    response: str


def build_moves(
    *,
    optimal_slot_by_id: dict[int, str],
    current_slot_by_id: dict[int, str],
    names: dict[int, str],
) -> list[LineupMove]:
    """The set of slot changes to turn the current lineup into the optimal one.

    `optimal_slot_by_id` maps every rostered player to the slot they *should* be
    in (a starting slot label, or "BE" if benched); `current_slot_by_id` is where
    ESPN has them now. Only players whose slot changes produce a move.
    """
    moves: list[LineupMove] = []
    for pid, target in optimal_slot_by_id.items():
        current = current_slot_by_id.get(pid, "BE")
        if slot_id_for(current) != slot_id_for(target):
            moves.append(LineupMove(pid, names.get(pid, str(pid)), current, target))
    return moves


def build_lineup_body(
    *, team_id: int, swid: str | None, scoring_period_id: int, moves: list[LineupMove]
) -> dict:
    """The ROSTER transaction ESPN expects for a set of lineup slot changes."""
    return {
        "isLeagueManager": False,
        "teamId": team_id,
        "type": "ROSTER",
        "memberId": (swid or "").strip() or None,
        "scoringPeriodId": scoring_period_id,
        "executionType": "EXECUTE",
        "items": [
            {
                "playerId": m.espn_player_id,
                "type": "LINEUP",
                "fromLineupSlotId": slot_id_for(m.from_slot),
                "toLineupSlotId": slot_id_for(m.to_slot),
            }
            for m in moves
        ],
    }


def set_lineup(
    *,
    season: int,
    league_id: int,
    team_id: int,
    swid: str | None,
    espn_s2: str | None,
    scoring_period_id: int,
    moves: list[LineupMove],
    transport: httpx.BaseTransport | None = None,
) -> LineupResult:
    """POST the lineup change to ESPN. Returns ESPN's raw (redacted) response."""
    url = transactions_url(season, league_id)
    if not LINEUP_WRITE_ENABLED:
        raise RuntimeError("Lineup writing is disabled (LINEUP_WRITE_ENABLED is False).")
    if not (swid and espn_s2):
        raise RuntimeError("ESPN cookies are missing; connect ESPN before setting a lineup.")
    if not moves:
        return LineupResult(True, 0, url, [], "No change needed -- lineup already optimal.")

    body = build_lineup_body(
        team_id=team_id, swid=swid, scoring_period_id=scoring_period_id, moves=moves
    )
    headers = {**_WRITE_HEADERS, "Cookie": f"espn_s2={espn_s2}; SWID={swid};"}
    try:
        with httpx.Client(timeout=_TIMEOUT, transport=transport, follow_redirects=True) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return LineupResult(False, 0, url, moves, redact(f"Could not reach ESPN: {exc}"))

    ok = 200 <= resp.status_code < 300
    log.info("Lineup write to team %s (league %s): HTTP %s", team_id, league_id, resp.status_code)
    return LineupResult(
        ok=ok, status_code=resp.status_code, url=url, moves=moves,
        response=redact((resp.text or "")[:1000]) or f"(empty body, HTTP {resp.status_code})",
    )
