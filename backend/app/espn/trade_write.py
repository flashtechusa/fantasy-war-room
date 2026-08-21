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

from dataclasses import dataclass

#: ESPN's write host -- the counterpart to FANTASY_READ_HOST. Its own web app
#: posts transactions here. Not called in Stage 1; named so Stage 2 has one
#: place to wire the send.
FANTASY_WRITE_HOST = "https://lm-api-writes.fantasy.espn.com"

#: Transactions live under the same league path as reads, plus /transactions/.
TRANSACTIONS_PATH = (
    "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}/transactions/"
)

#: The master switch for the live network write. Stays False until the payload
#: is verified against a real league. While False, `preview` is the only thing
#: that runs and it sends nothing.
SEND_ENABLED = False


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


def build_trade_body(
    *,
    my_team_id: int,
    their_team_id: int,
    swid: str | None,
    give: list[TradePlayer],
    receive: list[TradePlayer],
) -> dict:
    """The transaction payload ESPN expects for a proposed trade.

    Each traded player is one item with an explicit from/to team, so the payload
    is unambiguous about direction: players I give move me -> them, players I
    receive move them -> me.
    """
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
        "memberId": (swid or "").strip() or None,
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
    )
    return TradePreview(
        method="POST",
        url=transactions_url(season, league_id),
        body=body,
        summary=_summary(my_team_name, their_team_name, give, receive),
        sent=False,
        note=(
            "Preview only -- nothing was sent to ESPN. This shows exactly what "
            "would be submitted once live sending is enabled and confirmed."
        ),
    )
