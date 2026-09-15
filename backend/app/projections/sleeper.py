"""Sleeper as a projection source.

Free, no key, no account: Sleeper serves projections to anyone who asks, which
makes it the easiest second opinion to add and the natural default alongside
ESPN's public numbers.

Two caveats, stated plainly because they affect how much to trust this:

* **The projections endpoint is undocumented.** Sleeper documents leagues,
  drafts, players and stats; projections are served from the same host but are
  not in the docs, so the shape here is taken from what the endpoint returns in
  practice rather than from a contract. It could change without notice, which
  is why the parser ignores anything it does not recognise and returns nothing
  rather than raising when the shape is wrong.
* **Their stat keys are stable, their point totals are not ours.** Sleeper's
  `pts_ppr` and friends are scored under *their* assumed rules. Those are
  discarded on purpose -- what this module produces is raw stat lines keyed by
  ESPN stat id, so the app re-scores them under whichever league is active,
  exactly as it does for every other source.

The stat-key map below comes from Sleeper's league scoring settings, which
*are* documented and use the same names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..espn.constants import normalise_position

log = logging.getLogger(__name__)

SOURCE_KEY = "sleeper"

API_ROOT = "https://api.sleeper.app/projections/nfl"

#: Positions worth asking for. Sleeper will return IDP if asked; the engine
#: only values these, and a narrower request is a smaller response.
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

REQUEST_TIMEOUT = 30.0

#: Sleeper stat key -> ESPN stat id. Unlisted keys are ignored rather than
#: guessed at; a wrong mapping silently moves points around.
STAT_MAP: dict[str, int] = {
    # Passing
    "pass_att": 0,
    "pass_cmp": 1,
    "pass_inc": 2,
    "pass_yd": 3,
    "pass_td": 4,
    "pass_2pt": 19,
    "pass_int": 20,
    "pass_sack": 64,
    # Rushing
    "rush_att": 23,
    "rush_yd": 24,
    "rush_td": 25,
    "rush_2pt": 26,
    # Receiving
    "rec": 53,
    "rec_yd": 42,
    "rec_td": 43,
    "rec_2pt": 44,
    "rec_tgt": 58,
    # Fumbles
    "fum": 68,
    "fum_lost": 72,
    "fum_rec_td": 63,
    # Kicking. Sleeper's three sub-40 bands all live inside ESPN's 0-39 band,
    # which is fine here: these are counts, so they sum.
    "fgm_0_19": 80,
    "fgm_20_29": 80,
    "fgm_30_39": 80,
    "fgm_40_49": 77,
    "fgm_50p": 74,
    "fgmiss": 85,
    "xpm": 86,
    "xpa": 87,
    "xpmiss": 88,
    # Team defence / special teams
    "def_td": 94,
    "def_st_td": 105,
    "st_td": 105,
    "sack": 99,
    "int": 95,
    "fum_rec": 96,
    "safe": 98,
    "blk_kick": 97,
    "ff": 106,
    "pts_allow": 120,
    "pts_allow_0": 89,
    "pts_allow_1_6": 90,
    "pts_allow_7_13": 91,
    # Band edges differ slightly from ESPN's (14-17 / 22-27); the closest
    # equivalent is a better answer than dropping the category.
    "pts_allow_14_20": 92,
    "pts_allow_21_27": 122,
    "pts_allow_28_34": 123,
    "pts_allow_35p": 124,
    # Individual defence, for leagues that score it
    "idp_tkl_solo": 108,
    "idp_tkl_ast": 107,
    "idp_sack": 99,
    "idp_int": 95,
    "idp_fum_rec": 96,
    "idp_ff": 106,
    "idp_pass_def": 113,
}

#: Sleeper's own scored totals. Never stored: they are scored under Sleeper's
#: assumed rules, and keeping them invites them being used as a projection.
_SCORED_TOTALS = {"pts_ppr", "pts_half_ppr", "pts_std"}


class SleeperError(RuntimeError):
    """Raised when Sleeper's projections cannot be read."""


@dataclass
class SleeperProjection:
    """Same shape the other sources produce, so storage is shared."""

    name: str
    position: str
    pro_team: str
    raw_stats: dict[str, float] = field(default_factory=dict)
    projected_games: float | None = None


def fetch_projections(season: int, week: int | None = None) -> list[SleeperProjection]:
    """Projections for a season, or for one week when `week` is given."""
    path = f"{API_ROOT}/{int(season)}" + (f"/{int(week)}" if week else "")
    params: list[tuple[str, str]] = [("season_type", "regular"), ("order_by", "pts_ppr")]
    params.extend(("position[]", position) for position in POSITIONS)

    try:
        response = httpx.get(
            path,
            params=params,
            headers={"Accept": "application/json", "User-Agent": "FantasyWarRoom/1.0"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise SleeperError(f"Could not reach Sleeper: {exc}") from exc

    if response.status_code == 404:
        raise SleeperError(
            f"Sleeper has no projections for {season}"
            + (f" week {week}" if week else "")
            + " yet."
        )
    if response.status_code >= 400:
        raise SleeperError(f"Sleeper returned {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SleeperError("Sleeper returned a response we could not read.") from exc

    players = parse_projections(payload)
    if not players:
        raise SleeperError(
            "Sleeper returned no projections we could read. Their projections endpoint is "
            "undocumented, so this usually means its shape has changed."
        )
    return players


def parse_projections(payload) -> list[SleeperProjection]:
    """Turn a Sleeper response into player records.

    Kept separate from the HTTP call so it can be tested against captured
    shapes, and so an unexpected payload is a parsing problem rather than a
    network one.
    """
    entries = payload
    if isinstance(payload, dict):
        # Some responses nest the list; take the first list-shaped value rather
        # than assuming a key name that is not in any contract.
        entries = next(
            (value for value in payload.values() if isinstance(value, list)), None
        )
    if not isinstance(entries, list):
        log.warning("Sleeper payload had no recognisable projection list")
        return []

    out: list[SleeperProjection] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        record = _parse_entry(entry)
        if record is not None:
            out.append(record)
    return out


def _parse_entry(entry: dict) -> SleeperProjection | None:
    player = entry.get("player") if isinstance(entry.get("player"), dict) else {}

    position = normalise_position(
        _text(player.get("position")) or _text(entry.get("position"))
    )
    pro_team = (_text(player.get("team")) or _text(entry.get("team"))).upper()

    name = _full_name(player) or _text(entry.get("full_name"))
    if position == "DST" and not name:
        # Sleeper identifies a defence by its team abbreviation alone.
        name = pro_team
    if not name or position == "UNKNOWN":
        return None

    stats = entry.get("stats")
    if not isinstance(stats, dict):
        return None

    raw_stats: dict[str, float] = {}
    for key, value in stats.items():
        if key in _SCORED_TOTALS:
            continue
        stat_id = STAT_MAP.get(key)
        if stat_id is None:
            continue
        number = _float(value)
        if number:
            raw_stats[str(stat_id)] = raw_stats.get(str(stat_id), 0.0) + number

    if not raw_stats:
        return None

    games = _float(stats.get("gp")) or None
    return SleeperProjection(
        name=name,
        position=position,
        pro_team=pro_team or "FA",
        raw_stats=raw_stats,
        projected_games=min(games, 17.0) if games else None,
    )


def _full_name(player: dict) -> str:
    direct = _text(player.get("full_name"))
    if direct:
        return direct
    parts = [_text(player.get("first_name")), _text(player.get("last_name"))]
    return " ".join(part for part in parts if part).strip()


def _text(value) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
