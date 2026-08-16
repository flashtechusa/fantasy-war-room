"""ESPN id/label mappings we depend on directly.

`espn-api` already exposes POSITION_MAP / PRO_TEAM_MAP / SETTINGS_SCORING_FORMAT_MAP,
so we re-export rather than re-typing them.  What we add here is normalisation:
ESPN says "D/ST", the rest of the world says "DST", and its stat-name map is
lossy (several stat ids share a name), so we always work with integer stat ids.
"""

from __future__ import annotations

from espn_api.football.constant import (  # noqa: F401  (re-exported)
    POSITION_MAP,
    PRO_TEAM_MAP,
    SETTINGS_SCORING_FORMAT_MAP,
)

#: Positions the valuation engine understands.
FANTASY_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]

#: Positions eligible for a standard FLEX slot.
FLEX_ELIGIBLE = ["RB", "WR", "TE"]

#: ESPN lineup-slot id -> our position label.
SLOT_ID_TO_POSITION = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "DST",
    17: "K",
}

#: ESPN lineup-slot id -> slot label used on the League Settings screen.
SLOT_ID_TO_LABEL = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",  # superflex (offensive player)
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "DST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BE",
    21: "IR",
    23: "FLEX",
    24: "ER",
}

#: Multi-position slots -> which of our positions may fill them.
MULTI_SLOT_ELIGIBILITY = {
    "FLEX": ["RB", "WR", "TE"],
    "RB/WR": ["RB", "WR"],
    "WR/TE": ["WR", "TE"],
    "OP": ["QB", "RB", "WR", "TE"],
    "TQB": ["QB"],
}

#: Slots that don't consume a draftable starter (bench/IR/etc).
NON_STARTER_SLOTS = {"BE", "IR", "ER", ""}

#: ESPN defaultPositionId -> our position label.
DEFAULT_POSITION_ID_TO_POSITION = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DST",
}

#: Stat ids that indicate a reception, used to detect PPR without guessing.
RECEPTION_STAT_IDS = (53, 41)

INJURY_STATUS_LABELS = {
    "ACTIVE": "Active",
    "NORMAL": "Active",
    "QUESTIONABLE": "Questionable",
    "DOUBTFUL": "Doubtful",
    "OUT": "Out",
    "INJURY_RESERVE": "IR",
    "SUSPENSION": "Suspended",
    "DAY_TO_DAY": "Day to day",
    "PROBABLE": "Probable",
}


def normalise_position(raw: str | None) -> str:
    """Map any ESPN position spelling onto our canonical set."""
    if not raw:
        return "UNKNOWN"
    value = raw.strip().upper().replace(" ", "")
    if value in {"D/ST", "DST", "DEF", "D"}:
        return "DST"
    if value in {"PK", "K"}:
        return "K"
    return value


def normalise_slot_label(raw: str | None) -> str:
    """Map an ESPN lineup-slot label onto our canonical slot vocabulary."""
    if not raw:
        return ""
    value = raw.strip().upper().replace(" ", "")
    if value in {"D/ST", "DST"}:
        return "DST"
    if value == "RB/WR/TE":
        return "FLEX"
    if value == "PK":
        return "K"
    return value


def stat_label(stat_id: int) -> tuple[str, str]:
    """(abbrev, human label) for an ESPN scoring stat id."""
    entry = SETTINGS_SCORING_FORMAT_MAP.get(stat_id)
    if not entry:
        return (f"STAT{stat_id}", f"Stat {stat_id}")
    return (entry.get("abbr", f"STAT{stat_id}"), entry.get("label", f"Stat {stat_id}"))
