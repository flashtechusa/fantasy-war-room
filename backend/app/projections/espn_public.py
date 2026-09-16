"""ESPN's public projections, for leagues that are not on ESPN.

A Yahoo league has a problem an ESPN league does not: Yahoo publishes no
projections at all.  It will tell you who is on the wire, what they are owned
at and where they went in drafts, but not one projected stat.  Without a
projection there is nothing to rank, and the whole application is a very
elaborate list of names.

ESPN publishes its projections for a *default* league -- no league id, no
credentials -- which is exactly what is needed here.  We take the raw stat
lines and ignore ESPN's applied point totals entirely, because those are scored
under ESPN's default rules; re-scoring the raw stats under the Yahoo league's
own rules is the same thing the app already does everywhere else.

Players are matched by name, which is why the importer reports its match rate:
a projection stapled to the wrong player is undetectable downstream.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from ..espn.constants import (
    DEFAULT_POSITION_ID_TO_POSITION,
    PRO_TEAM_MAP,
    SLOT_ID_TO_LABEL,
    normalise_position,
)

log = logging.getLogger(__name__)

SOURCE_KEY = "espn_public"

#: ESPN's read replica. `leaguedefaults/3` is a default PPR league, which is
#: what makes this readable without a league or a cookie. Only the raw stat
#: lines are used, and those do not depend on the default league's rules.
API_ROOT = "https://lm-api-reads.espn.com/apis/v3/games/ffl/seasons"
LEAGUE_DEFAULT = 3

REQUEST_TIMEOUT = 30.0


class EspnPublicError(RuntimeError):
    """Raised when ESPN's public projections cannot be read."""


@dataclass
class PublicProjection:
    """Same shape the FantasyPros source produces, so storage is shared."""

    name: str
    position: str
    pro_team: str
    raw_stats: dict[str, float] = field(default_factory=dict)
    projected_games: float | None = None


def fetch_projections(season: int, limit: int = 900) -> list[PublicProjection]:
    """Season-long projections for the top `limit` players by draft rank."""
    url = f"{API_ROOT}/{season}/segments/0/leaguedefaults/{LEAGUE_DEFAULT}"
    headers = {
        "x-fantasy-filter": json.dumps(
            {
                "players": {
                    "limit": int(limit),
                    "sortDraftRanks": {
                        "sortPriority": 100,
                        "sortAsc": True,
                        "value": "PPR",
                    },
                }
            }
        ),
        # ESPN's read replica rejects requests without a browser-ish UA.
        "User-Agent": "Mozilla/5.0 (compatible; FantasyWarRoom/1.0)",
        "Accept": "application/json",
    }
    try:
        response = httpx.get(
            url,
            params={"view": "kona_player_info"},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise EspnPublicError(f"Could not reach ESPN for public projections: {exc}") from exc

    if response.status_code >= 400:
        raise EspnPublicError(
            f"ESPN returned {response.status_code} for its public projections."
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise EspnPublicError("ESPN returned a response we could not read.") from exc

    players = parse_projections(payload, season)
    if not players:
        raise EspnPublicError(
            f"ESPN published no projections for {season}. They usually appear a few weeks "
            "before the season opens."
        )
    return players


def parse_projections(payload: dict, season: int) -> list[PublicProjection]:
    """Parse the `kona_player_info` payload. Kept separate so it can be tested.

    Only the full-season projection is taken (`statSourceId == 1`,
    `statSplitTypeId == 0`); actuals and weekly splits are ignored, as is
    ESPN's own applied total.
    """
    entries = payload.get("players") or []
    if not isinstance(entries, list):
        log.warning("ESPN public payload had no player list")
        return []

    out: list[PublicProjection] = []
    for entry in entries:
        player = (entry or {}).get("player") or {}
        name = str(player.get("fullName") or "").strip()
        if not name:
            continue

        position = DEFAULT_POSITION_ID_TO_POSITION.get(
            _int(player.get("defaultPositionId"), -1)
        )
        if position is None:
            for slot_id in player.get("eligibleSlots") or []:
                label = SLOT_ID_TO_LABEL.get(_int(slot_id, -1), "")
                if label in {"QB", "RB", "WR", "TE", "K", "DST"}:
                    position = label
                    break
        if position is None:
            continue

        pro_team = PRO_TEAM_MAP.get(_int(player.get("proTeamId"), 0), "FA")
        if pro_team == "None":
            pro_team = "FA"

        raw_stats, games = _season_projection(player, season)
        if not raw_stats:
            continue

        out.append(
            PublicProjection(
                name=name,
                position=normalise_position(position),
                pro_team=pro_team,
                raw_stats=raw_stats,
                projected_games=games,
            )
        )
    return out


def _season_projection(player: dict, season: int) -> tuple[dict[str, float], float]:
    for stats in player.get("stats") or []:
        if _int(stats.get("statSourceId"), -1) != 1:
            continue
        if _int(stats.get("seasonId"), -1) != season:
            continue
        if _int(stats.get("statSplitTypeId"), -1) != 0:
            continue
        if _int(stats.get("scoringPeriodId"), -1) != 0:
            continue
        raw = {str(k): _float(v) for k, v in (stats.get("stats") or {}).items()}
        total = _float(stats.get("appliedTotal"))
        average = _float(stats.get("appliedAverage"))
        games = total / average if (average > 0.01 and total > 0) else 17.0
        return (raw, max(1.0, min(17.0, round(games, 1))))
    return ({}, 17.0)


def _int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
