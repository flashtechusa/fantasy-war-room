"""Reading ESPN's draft board directly from `view=mDraftDetail`.

Why this is a separate path from the league import
--------------------------------------------------
`espn-api` is our primary source for a *completed* draft and it works well.
It cannot be our source during a *live* one, for two reasons found by reading
its source (`espn_api/base_league.py`):

* `_fetch_draft()` returns immediately unless `draftDetail.drafted` is true.
  ESPN only sets that flag when the draft finishes, so for the entire duration
  of a live draft the library reports zero picks -- even though `picks` is
  populated pick by pick as the draft runs.
* `_fetch_draft()` appends to `league.draft` without clearing it, and
  `refresh_draft()` calls it again. Polling therefore duplicates every pick on
  every poll, growing the list without bound.

Both are avoided here by parsing the payload ourselves. `mDraftDetail` is also
the cheapest useful endpoint ESPN exposes for this: it carries no player
objects, so a 200-pick board is a few tens of kilobytes.

The payload also gives us `overallPickNumber` directly, which is strictly
better than deriving it from round/pick: derivation assumes a snake order with
no traded picks, and is meaningless in an auction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


def _i(value, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class DraftFeedSnapshot:
    """One read of the draft board.

    `picks` is always what ESPN currently shows, whether or not the draft has
    finished -- that distinction lives in the flags instead, so a caller can
    tell "no picks yet" from "draft not started".
    """

    picks: list[dict] = field(default_factory=list)
    #: ESPN's own flags. `drafted` means complete; `in_progress` means running.
    drafted: bool = False
    in_progress: bool = False
    draft_type: str = ""
    #: Round ESPN says is current, when it tells us.
    current_round: int | None = None
    #: Team on the clock, when ESPN tells us.
    on_the_clock_team_id: int | None = None
    latency_ms: float = 0.0
    #: Already redacted by the HTTP layer; safe to display and store.
    endpoint: str = ""
    source: str = "espn_draft_detail"

    @property
    def pick_count(self) -> int:
        return len(self.picks)

    @property
    def latest_pick_number(self) -> int:
        return max((p.get("overall_pick") or 0 for p in self.picks), default=0)

    @property
    def started(self) -> bool:
        return bool(self.picks) or self.in_progress or self.drafted


def parse_draft_detail(
    payload: dict,
    season: int,
    team_count: int = 0,
    team_names: dict[int, str] | None = None,
    player_names: dict[int, str] | None = None,
) -> DraftFeedSnapshot:
    """Turn an `mDraftDetail` payload into picks in our own shape.

    `team_names` / `player_names` are optional lookups from our database.
    ESPN's draft view carries ids only, and the whole point of this endpoint is
    that it stays small -- so names are filled in locally rather than by asking
    ESPN for a second, much larger payload.
    """
    detail = (payload or {}).get("draftDetail") or {}
    settings = ((payload or {}).get("settings") or {}).get("draftSettings") or {}
    team_names = team_names or {}
    player_names = player_names or {}

    picks: list[dict] = []
    for index, raw in enumerate(detail.get("picks") or []):
        if not isinstance(raw, dict):
            continue
        player_id = _i(raw.get("playerId"))
        # ESPN emits placeholder rows for picks that have not happened yet;
        # they carry a playerId of 0 or -1. Treating those as real picks would
        # make the board look complete before the draft started.
        if not player_id or player_id <= 0:
            continue

        round_num = _i(raw.get("roundId"), 0) or 0
        round_pick = _i(raw.get("roundPickNumber"), 0) or 0
        overall = _i(raw.get("overallPickNumber"), 0) or 0
        if overall <= 0:
            # Only derive when ESPN did not say. Derivation assumes an
            # untraded snake order, which is why it is the fallback.
            overall = (
                (round_num - 1) * team_count + round_pick
                if round_num and round_pick and team_count
                else index + 1
            )

        team_id = _i(raw.get("teamId"))
        picks.append(
            {
                "season": int(season),
                "overall_pick": overall,
                "round_num": round_num,
                "round_pick": round_pick,
                "espn_team_id": team_id,
                "team_name": team_names.get(team_id, "") if team_id else "",
                "espn_player_id": player_id,
                "player_name": player_names.get(player_id, ""),
                "bid_amount": _i(raw.get("bidAmount"), 0) or 0,
                "keeper": bool(raw.get("keeper")),
                "auto_pick": bool(raw.get("autoDraftTypeId")),
                "nominating_team_id": _i(raw.get("nominatingTeamId")),
            }
        )

    picks.sort(key=lambda p: p["overall_pick"])

    return DraftFeedSnapshot(
        picks=picks,
        drafted=bool(detail.get("drafted")),
        in_progress=bool(detail.get("inProgress")),
        draft_type=str(settings.get("type") or "").upper(),
        current_round=_i(detail.get("currentRound")),
        on_the_clock_team_id=_i(detail.get("onTheClockTeamId")),
    )


def fetch_draft_snapshot(
    client,
    league_id: int,
    season: int,
    team_count: int = 0,
    team_names: dict[int, str] | None = None,
    player_names: dict[int, str] | None = None,
) -> DraftFeedSnapshot:
    """One `mDraftDetail` request, parsed, with its latency recorded.

    `client` is an `EspnHttpClient`; it is passed in rather than constructed
    so a live draft reuses one connection pool across polls.
    """
    response = client.draft_detail(league_id, season)
    snapshot = parse_draft_detail(
        response.data,
        season=season,
        team_count=team_count,
        team_names=team_names,
        player_names=player_names,
    )
    snapshot.latency_ms = response.latency_ms
    snapshot.endpoint = response.url
    return snapshot
