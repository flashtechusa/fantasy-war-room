"""Building a trade-proposal write for ESPN -- staged, preview first.

Everything else in this app only ever *reads* from ESPN. This is the one place
that constructs a *write*: a trade proposal, sent to another manager's real
league. That is an irreversible, outward-facing action, so it is built in two
stages and this module is the first:

    STAGE 1 (this module, now): build the exact transaction payload and return
    it for the user to inspect. Nothing is sent. `SEND_ENABLED` is False.

    STAGE 2 (later, once the payload is validated against a live league): call
    the writes host with these cookies behind a hard confirmation.

Keeping the builder pure and separate from any HTTP call is deliberate: the
risky part (the network write) can be reviewed and enabled on its own, and the
payload can be unit-tested without ever touching ESPN.

The payload shape is reverse-engineered from what ESPN's own web app sends when
you propose a trade; ESPN publishes no official write API, so it must be
verified against a real proposal before Stage 2 is switched on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .redaction import SWID_PLACEHOLDER, redact

log = logging.getLogger(__name__)

#: ESPN's write host -- the counterpart to FANTASY_READ_HOST. Its own web app
#: posts transactions here.
FANTASY_WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"

#: Transactions live under the same league path as reads, plus /transactions/.
TRANSACTIONS_PATH = (
    "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}/transactions/"
)

#: The master switch for the live network write. Stage 2 turns this on; the send
#: is still guarded by the per-user capability and a hard confirmation, and it
#: always surfaces ESPN's raw response so a rejected shape is visible, not silent.
SEND_ENABLED = True

#: Headers ESPN's own web client sends with a fantasy write. Content-Type is
#: required; the X-Fantasy-* hints mirror what the site sends. If ESPN rejects a
#: write, these are the first thing to adjust -- which is why the send returns
#: ESPN's exact response rather than swallowing it.
_WRITE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "X-Fantasy-Source": "kona",
    "X-Fantasy-Platform": "kona-PROD",
}
_TIMEOUT = 20.0


@dataclass
class TradePlayer:
    """One player moving in a trade, with a name for the human-readable summary."""

    espn_player_id: int
    name: str
    position: str


@dataclass
class TradePreview:
    """The built proposal: what would be sent, and a plain-English summary.

    `sent` is always False in Stage 1. `body` is the exact JSON that Stage 2
    would POST, so it can be eyeballed before any live write exists.
    """

    method: str
    url: str
    body: dict
    summary: str
    sent: bool
    note: str


def transactions_url(season: int, league_id: int) -> str:
    return FANTASY_WRITE_HOST + TRANSACTIONS_PATH.format(season=season, league_id=league_id)


def fingerprint(
    *,
    season: int,
    league_id: int,
    my_team_id: int,
    their_team_id: int,
    give_ids: list[int],
    receive_ids: list[int],
) -> str:
    """A stable hash of the proposal's identity, for duplicate-send detection.

    Player ids are sorted so the same trade always hashes the same regardless of
    selection order. Contains no credentials -- only public league/team/player
    ids -- so it is safe to store in the audit log.
    """
    import hashlib

    parts = [
        str(season), str(league_id), str(my_team_id), str(their_team_id),
        ",".join(str(i) for i in sorted(give_ids)),
        ",".join(str(i) for i in sorted(receive_ids)),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def build_trade_body(
    *,
    my_team_id: int,
    their_team_id: int,
    swid: str | None,
    give: list[TradePlayer],
    receive: list[TradePlayer],
    mask_member: bool = False,
) -> dict:
    """The transaction payload ESPN expects for a proposed trade.

    Each traded player is one item with an explicit from/to team, so the payload
    is unambiguous about direction: players I give move me -> them, players I
    receive move them -> me. `mask_member` replaces the SWID in the preview so a
    live session credential is never shown or logged; the real send builds it
    with the true SWID server-side.
    """
    member = SWID_PLACEHOLDER if mask_member else ((swid or "").strip() or None)
    items = [
        {
            "playerId": p.espn_player_id,
            "type": "TRADE",
            "fromTeamId": my_team_id,
            "toTeamId": their_team_id,
        }
        for p in give
    ] + [
        {
            "playerId": p.espn_player_id,
            "type": "TRADE",
            "fromTeamId": their_team_id,
            "toTeamId": my_team_id,
        }
        for p in receive
    ]
    return {
        "type": "TRADE_PROPOSAL",
        "isLeagueManager": False,
        "memberId": member,
        "teamId": my_team_id,
        "proposingTeamId": my_team_id,
        "acceptingTeamId": their_team_id,
        "items": items,
        "scoringPeriodId": 0,
    }


def _summary(my_name: str, their_name: str, give: list[TradePlayer], receive: list[TradePlayer]) -> str:
    give_s = ", ".join(f"{p.name} ({p.position})" for p in give) or "nobody"
    recv_s = ", ".join(f"{p.name} ({p.position})" for p in receive) or "nobody"
    return (
        f"Propose to {their_name}: you send {give_s}; you receive {recv_s}. "
        f"(from your team {my_name})"
    )


def preview_trade(
    *,
    season: int,
    league_id: int,
    my_team_id: int,
    my_team_name: str,
    their_team_id: int,
    their_team_name: str,
    swid: str | None,
    give: list[TradePlayer],
    receive: list[TradePlayer],
) -> TradePreview:
    """Stage 1: build the proposal and describe it. Sends nothing.

    Returns the exact request that a live send would make, so the user can
    confirm it is correct before any network write is enabled.
    """
    body = build_trade_body(
        my_team_id=my_team_id,
        their_team_id=their_team_id,
        swid=swid,
        give=give,
        receive=receive,
        mask_member=True,  # never show or log a live SWID
    )
    return TradePreview(
        method="POST",
        url=transactions_url(season, league_id),
        body=body,
        summary=_summary(my_team_name, their_team_name, give, receive),
        sent=False,
        note=(
            "Preview only -- nothing was sent to ESPN. This shows exactly what "
            "would be submitted; your ESPN id is masked here and filled in only "
            "at send time."
        ),
    )


@dataclass
class SendResult:
    """The outcome of a live send: whether ESPN accepted it, and its raw reply.

    `response` is always redacted, so it is safe to show and to log even though
    ESPN may echo request material back.
    """

    ok: bool
    status_code: int
    url: str
    summary: str
    response: str


def send_trade(
    *,
    season: int,
    league_id: int,
    my_team_id: int,
    my_team_name: str,
    their_team_id: int,
    their_team_name: str,
    swid: str | None,
    espn_s2: str | None,
    give: list[TradePlayer],
    receive: list[TradePlayer],
    transport: httpx.BaseTransport | None = None,
) -> SendResult:
    """Stage 2: actually POST the trade proposal to ESPN.

    Guarded by `SEND_ENABLED` and, at the route, by the per-user capability and a
    hard confirmation. Returns ESPN's exact (redacted) response so a rejected
    payload shape is visible and can be corrected, rather than failing silently.
    """
    if not SEND_ENABLED:
        raise RuntimeError("Live sending is disabled (SEND_ENABLED is False).")
    if not (swid and espn_s2):
        raise RuntimeError("ESPN cookies are missing; connect ESPN before sending.")

    url = transactions_url(season, league_id)
    body = build_trade_body(
        my_team_id=my_team_id,
        their_team_id=their_team_id,
        swid=swid,
        give=give,
        receive=receive,
    )
    headers = {
        **_WRITE_HEADERS,
        "Cookie": f"espn_s2={espn_s2}; SWID={swid};",
    }
    summary = _summary(my_team_name, their_team_name, give, receive)
    try:
        with httpx.Client(timeout=_TIMEOUT, transport=transport, follow_redirects=True) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        # Never let a raw exception (which may quote the cookie header) escape.
        return SendResult(
            ok=False, status_code=0, url=url, summary=summary,
            response=redact(f"Could not reach ESPN: {exc}"),
        )

    text = resp.text or ""
    ok = 200 <= resp.status_code < 300
    log.info(
        "Trade proposal to team %s (league %s): HTTP %s",
        their_team_id, league_id, resp.status_code,
    )
    return SendResult(
        ok=ok,
        status_code=resp.status_code,
        url=url,
        summary=summary,
        response=redact(text[:1000]) or f"(empty body, HTTP {resp.status_code})",
    )
