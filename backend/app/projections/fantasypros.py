"""FantasyPros as a second projection source.

**Bring your own key.** No key is bundled or shared. Each installation supplies
its own, obtained under its own agreement with FantasyPros, and the source is
inert until one is configured. FantasyPros' free keys are issued for personal,
non-commercial use, so whether a given deployment may use this at all is a
question for whoever holds the key -- which is why it is opt-in, per-install,
and disabled by default.

What this module produces is raw stat lines keyed by ESPN stat id. It
deliberately discards FantasyPros' own point totals: those are scored under
their assumed rules, and re-scoring raw stats under the league's actual rules
is the whole point of this application.

The stat map is taken from a real response rather than their documentation.
An earlier version guessed the names from the docs and got most of them wrong
(`rush_td` for `rush_tds`, `rec` for `rec_rec`), which meant every player
matched and every stat line came back empty while the import reported success.
The parser stays defensive -- unknown keys are ignored, and a shape it cannot
read returns nothing rather than silently producing empty projections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..espn.constants import normalise_position

log = logging.getLogger(__name__)

API_ROOT = "https://api.fantasypros.com/public/v2/json/nfl"
SOURCE_KEY = "fantasypros"

#: FantasyPros stat name -> ESPN stat id.
#:
#: Taken from a real response, not their docs. Their names are plural
#: (`rush_tds`, `rec_tds`) and receptions are `rec_rec`, which an earlier
#: guess got wrong -- with the result that every player matched and every
#: stat line came back empty, while the import reported success. Singular
#: spellings are kept alongside in case other positions differ; an unknown
#: name is ignored rather than guessed at.
STAT_MAP: dict[str, int] = {
    # Passing
    "pass_yds": 3,
    "pass_tds": 4,
    "pass_td": 4,
    "pass_int": 20,
    "pass_ints": 20,
    "pass_att": 0,
    "pass_cmp": 1,
    "pass_2pt": 19,
    "pass_2pts": 19,
    # Rushing
    "rush_yds": 24,
    "rush_tds": 25,
    "rush_td": 25,
    "rush_2pt": 26,
    # Receiving -- `rec_rec` is the reception count, not `rec`.
    "rec_rec": 53,
    "rec": 53,
    "rec_yds": 42,
    "rec_tds": 43,
    "rec_td": 43,
    "rec_2pt": 44,
    # Misc. FantasyPros projects `fumbles` as fumbles lost.
    "fumbles": 72,
    "fumbles_lost": 72,
    "fum_lost": 72,
    "ret_tds": 105,
    # Kicking
    "fg": 80,
    "fgs": 80,
    "fg_miss": 85,
    "xpt": 86,
    "xpts": 86,
    "xpt_miss": 88,
    # Defence / special teams
    "sack": 99,
    "sacks": 99,
    "int": 95,
    "ints": 95,
    "fum_rec": 96,
    "fum_recs": 96,
    "def_td": 94,
    "def_tds": 94,
    "safety": 98,
    "safeties": 98,
    "pts_agn": 120,
    "pts_vs": 120,
    "yds_agn": 127,
    "yds_vs": 127,
}

#: Their own point totals, under their assumed rules. Deliberately never
#: stored -- re-scoring raw stats under the league's rules is the point -- but
#: named here so they are recognisably excluded rather than accidentally
#: swept up as a stat.
IGNORED_KEYS = frozenset(
    {
        "points",
        "points_ppr",
        "points_half",
        "rush_yds_100",
        "rush_yds_200",
        "rec_yds_100",
        "rec_yds_200",
        "scrimage_yards_100",
        "scrimage_yards_200",
        # Combined two-point total: ESPN scores passing, rushing and receiving
        # conversions separately, so this cannot be attributed to one of them.
        "2pt_tds",
    }
)

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


class FantasyProsError(RuntimeError):
    """Raised when FantasyPros cannot be reached or refuses the key."""


@dataclass
class FantasyProsPlayer:
    name: str
    position: str
    pro_team: str
    raw_stats: dict[str, float] = field(default_factory=dict)
    projected_games: float | None = None


def _f(value: object) -> float:
    """FantasyPros returns numbers as strings, sometimes with commas."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip() or 0.0)
    except ValueError:
        return 0.0


class FantasyProsClient:
    """Thin, defensive client. One request per position."""

    def __init__(self, api_key: str, season: int, *, timeout: float = 20.0) -> None:
        if not api_key:
            raise FantasyProsError("No FantasyPros API key configured.")
        self.api_key = api_key
        self.season = season
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> dict:
        url = f"{API_ROOT}/{self.season}/{path}"
        try:
            response = httpx.get(
                url,
                params=params,
                headers={"x-api-key": self.api_key},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise FantasyProsError(f"Could not reach FantasyPros: {exc}") from exc

        if response.status_code in (401, 403):
            raise FantasyProsError(
                "FantasyPros rejected the API key (check it is valid and still active)."
            )
        if response.status_code == 429:
            raise FantasyProsError(
                "FantasyPros rate limit reached. Free keys allow 50 requests a day."
            )
        if response.status_code >= 400:
            raise FantasyProsError(
                f"FantasyPros returned {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise FantasyProsError("FantasyPros returned a response that was not JSON.") from exc

    def projections(self, week: int | str = "draft") -> list[FantasyProsPlayer]:
        """Season (or weekly) projections for every position.

        `week="draft"` is their preseason full-season projection; an integer is
        that week. Six requests total -- worth knowing against a 50/day cap.
        """
        out: list[FantasyProsPlayer] = []
        for position in POSITIONS:
            payload = self._get(
                "projections",
                {"position": position, "week": week, "scoring": "STD"},
            )
            out.extend(parse_projections(payload, position))
        return out


def parse_projections(payload: dict, position_hint: str = "") -> list[FantasyProsPlayer]:
    """Turn one FantasyPros response into player records.

    Kept separate from the HTTP client so it can be tested against captured
    fixtures without a key, and so an unexpected shape is a parsing problem
    rather than a network one.
    """
    players_raw = payload.get("players")
    if players_raw is None:
        # Some endpoints nest under a different key; try the obvious ones
        # before giving up, but never invent data.
        for alternative in ("data", "projections", "results"):
            if isinstance(payload.get(alternative), list):
                players_raw = payload[alternative]
                break
    if not isinstance(players_raw, list):
        log.warning("FantasyPros payload had no recognisable player list")
        return []

    out: list[FantasyProsPlayer] = []
    for entry in players_raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("player_name") or ""
        if not name:
            continue
        position = normalise_position(
            entry.get("position_id") or entry.get("position") or position_hint
        )
        team = entry.get("team_id") or entry.get("team") or ""

        stats = entry.get("stats")
        if not isinstance(stats, dict):
            stats = entry  # some responses inline the stats

        raw: dict[str, float] = {}
        for source_name, stat_id in STAT_MAP.items():
            if source_name in stats:
                value = _f(stats.get(source_name))
                if value:
                    # Keys are strings to match how ESPN projections are stored.
                    raw[str(stat_id)] = raw.get(str(stat_id), 0.0) + value

        games = entry.get("games") or stats.get("games")
        out.append(
            FantasyProsPlayer(
                name=str(name),
                position=position,
                pro_team=str(team),
                raw_stats=raw,
                projected_games=_f(games) or None,
            )
        )
    return out
