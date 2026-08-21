"""Sleeper as an isolated, optional projection source.

Sleeper publishes per-player projections on a public, key-less endpoint. Like
every other non-ESPN source in this app, what we take from it is the **raw
component stat line** (passing yards, receptions, ...), never its point totals:
Sleeper's `pts_ppr` / `pts_std` / `pts_half_ppr` are scored under *its* assumed
rules, and the whole point of this application is to re-score raw stats under
the connected league's actual rules. So those precomputed totals are discarded
(see `IGNORED_KEYS`).

**Scope, deliberately narrow.** The stat map covers offensive skill stats
(QB/RB/WR/TE) using the same ESPN stat ids the FantasyPros adapter already uses
with confidence. Kicking and D/ST are intentionally *not* mapped: ESPN scores
field goals by distance with stat ids this module will not guess at, and a
half-mapped kicker would score worse than one left alone. A player whose stat
line maps to nothing produces no projection row, so downstream it simply falls
back to the existing (ESPN) projection. That keeps this source honest -- it
never invents numbers it cannot map correctly.

This module only *reads* Sleeper, sends no credentials, and writes nothing back.
It is inert unless the "Use Sleeper Projections" toggle turns it on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..espn.constants import normalise_position

log = logging.getLogger(__name__)

API_ROOT = "https://api.sleeper.com"
SOURCE_KEY = "sleeper"
SOURCE_LABEL = "Sleeper projections"

#: The positions we ask Sleeper for and re-score. Offensive skill only -- see
#: the module docstring for why K/DST are excluded.
POSITIONS = ("QB", "RB", "WR", "TE")

#: Sleeper stat name -> ESPN stat id. Only stats we can map with confidence to
#: the same ESPN ids the FantasyPros adapter uses; an unknown key is ignored
#: rather than guessed at. Sleeper's names are singular (`pass_yd`, `rush_yd`,
#: `rec_yd`) and its reception count is plain `rec`.
STAT_MAP: dict[str, int] = {
    # Passing
    "pass_yd": 3,
    "pass_td": 4,
    "pass_int": 20,
    "pass_2pt": 19,
    "pass_att": 0,
    "pass_cmp": 1,
    # Rushing
    "rush_yd": 24,
    "rush_td": 25,
    "rush_2pt": 26,
    # Receiving -- `rec` is the reception count (ESPN stat 53).
    "rec": 53,
    "rec_yd": 42,
    "rec_td": 43,
    "rec_2pt": 44,
    # Turnovers
    "fum_lost": 72,
}

#: Sleeper's own point totals and bonuses. Never stored: re-scoring the raw
#: stats under the league's rules is the point. Named so they are recognisably
#: excluded rather than accidentally swept up. Anything not in `STAT_MAP` is
#: ignored anyway; this set exists for clarity and defensiveness.
IGNORED_KEYS = frozenset(
    {
        "pts_ppr",
        "pts_half_ppr",
        "pts_std",
        "pts_dynasty_ppr",
        "pts_dynasty_half_ppr",
        "pts_dynasty_std",
        "adp_dd_ppr",
        "gp",
        "gms_active",
        "pass_fd",
        "rush_fd",
        "rec_fd",
        "rec_tgt",
        "rush_att",
        "bonus_rec_te",
    }
)


class SleeperError(RuntimeError):
    """Raised when Sleeper cannot be reached or answers unusably."""


@dataclass
class SleeperPlayer:
    name: str
    position: str
    pro_team: str
    raw_stats: dict[str, float] = field(default_factory=dict)
    projected_games: float | None = None


def _f(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip() or 0.0)
    except ValueError:
        return 0.0


class SleeperProjectionsClient:
    """Thin, read-only client for Sleeper's public projections endpoint.

    No API key exists or is needed. One request covers all skill positions.
    """

    def __init__(self, season: int, *, timeout: float = 25.0, transport=None) -> None:
        self.season = int(season)
        self.timeout = timeout
        self._transport = transport

    def _get(self, path: str, params: list[tuple[str, str]]) -> object:
        url = f"{API_ROOT}{path}"
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.get(
                    url, params=params, headers={"User-Agent": "fantasy-war-room"}
                )
        except httpx.HTTPError as exc:
            raise SleeperError(f"Could not reach Sleeper: {exc}") from exc

        if response.status_code >= 400:
            raise SleeperError(
                f"Sleeper returned {response.status_code} for {path}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SleeperError("Sleeper returned a response that was not JSON.") from exc

    def projections(self, week: int | None = None) -> list[SleeperPlayer]:
        """Season projections (week=None) or one week's, for the skill positions.

        Season totals are the right basis for the draft board and season-long
        valuation; a specific week is available for in-season comparison.
        """
        path = (
            f"/projections/nfl/{self.season}"
            if week is None
            else f"/projections/nfl/{self.season}/{int(week)}"
        )
        params: list[tuple[str, str]] = [("season_type", "regular"), ("order_by", "pts_ppr")]
        params.extend(("position[]", pos) for pos in POSITIONS)
        payload = self._get(path, params)
        return parse_projections(payload)


def parse_projections(payload: object) -> list[SleeperPlayer]:
    """Turn a Sleeper projections response into raw stat lines.

    Kept HTTP-independent so it can be tested against captured fixtures. An
    unrecognised shape returns nothing rather than inventing data.
    """
    if isinstance(payload, dict):
        # Some responses key rows by player id; take the values.
        rows = list(payload.values())
    elif isinstance(payload, list):
        rows = payload
    else:
        log.warning("Sleeper projections payload had an unrecognised shape")
        return []

    out: list[SleeperPlayer] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        player = entry.get("player") or {}
        name = " ".join(
            p for p in [player.get("first_name"), player.get("last_name")] if p
        ).strip() or entry.get("player_name") or ""
        if not name:
            continue
        position = normalise_position(player.get("position") or entry.get("position") or "")
        team = player.get("team") or entry.get("team") or ""

        stats = entry.get("stats")
        if not isinstance(stats, dict):
            continue

        raw: dict[str, float] = {}
        for source_name, stat_id in STAT_MAP.items():
            if source_name in stats:
                value = _f(stats.get(source_name))
                if value:
                    # Keys are strings to match how ESPN projections are stored.
                    raw[str(stat_id)] = raw.get(str(stat_id), 0.0) + value

        games = stats.get("gp") or stats.get("gms_active")
        out.append(
            SleeperPlayer(
                name=str(name),
                position=position,
                pro_team=str(team),
                raw_stats=raw,
                projected_games=_f(games) or None,
            )
        )
    return out
