"""Translating Yahoo's vocabulary into the one this app already speaks.

The engine scores raw stat lines by *stat id*, and those ids are ESPN's --
every projection source in the app (ESPN, FantasyPros) already emits them.
Rather than teach the engine a second numbering, Yahoo is translated at the
edge: Yahoo stat id -> ESPN stat id, Yahoo roster position -> our slot label.
That is what lets a Yahoo league use ESPN or FantasyPros projections, and what
keeps `LeagueScoring`, `LeagueShape` and the valuation engine untouched.

Two honest caveats, both surfaced rather than hidden:

* **Bucket mismatches.** Yahoo scores field goals in 10-yard bands (0-19,
  20-29, 30-39); ESPN's narrowest band is 0-39.  Where several Yahoo
  categories collapse onto one ESPN id they are combined, and the collision is
  reported on the league so the League screen can show it.
* **Yahoo-only categories.** Anything with no ESPN equivalent (IDP tackles,
  return yardage bands, ...) keeps its own id in a private range so the rule
  survives the round trip and can be listed -- but no projection source
  supplies those stats, so they score zero.  Reporting them beats a scoring
  page that silently omits rules the league actually uses.
"""

from __future__ import annotations

#: Yahoo stat id -> ESPN stat id.  Only unambiguous equivalences are listed;
#: an unmapped id falls into the private range below rather than being guessed.
YAHOO_TO_ESPN_STAT: dict[int, int] = {
    # -- passing --------------------------------------------------------
    1: 0,     # Passing Attempts
    2: 1,     # Completions
    3: 2,     # Incomplete Passes
    4: 3,     # Passing Yards
    5: 4,     # Passing Touchdowns
    6: 20,    # Interceptions thrown
    7: 64,    # Sacked
    # -- rushing --------------------------------------------------------
    8: 23,    # Rushing Attempts
    9: 24,    # Rushing Yards
    10: 25,   # Rushing Touchdowns
    # -- receiving ------------------------------------------------------
    11: 53,   # Receptions          (ESPN 53 is the per-reception rule)
    12: 42,   # Receiving Yards
    13: 43,   # Receiving Touchdowns
    78: 58,   # Targets
    # -- misc offence ---------------------------------------------------
    15: 105,  # Return Touchdowns (total)
    16: 62,   # 2-Point Conversions (total)
    17: 68,   # Fumbles
    18: 72,   # Fumbles Lost
    57: 63,   # Offensive Fumble Return TD
    # -- kicking --------------------------------------------------------
    # Yahoo's 0-19 / 20-29 / 30-39 bands all live inside ESPN's 0-39 band.
    19: 80,   # FG 0-19   -> FG Made (0-39)
    20: 80,   # FG 20-29  -> FG Made (0-39)
    21: 80,   # FG 30-39  -> FG Made (0-39)
    22: 77,   # FG 40-49  -> FG Made (40-49)
    23: 74,   # FG 50+    -> FG Made (50+)
    24: 82,   # FG Missed 0-19  -> FG Missed (0-39)
    25: 82,   # FG Missed 20-29 -> FG Missed (0-39)
    26: 82,   # FG Missed 30-39 -> FG Missed (0-39)
    27: 79,   # FG Missed 40-49
    28: 76,   # FG Missed 50+
    29: 86,   # PAT Made
    30: 88,   # PAT Missed
    # -- team defence / special teams ------------------------------------
    31: 120,  # Points Allowed
    32: 99,   # Sack
    33: 95,   # Interception
    34: 96,   # Fumble Recovery
    35: 94,   # Defensive/ST Touchdown
    36: 98,   # Safety
    37: 97,   # Blocked Kick
    38: 108,  # Solo Tackles
    39: 107,  # Assisted Tackles
    50: 89,   # Points Allowed 0
    51: 90,   # Points Allowed 1-6
    52: 91,   # Points Allowed 7-13   -> ESPN 7-13
    53: 92,   # Points Allowed 14-20  -> ESPN 14-17 (band edges differ)
    54: 122,  # Points Allowed 21-27  -> ESPN 22-27 (band edges differ)
    55: 123,  # Points Allowed 28-34
    56: 124,  # Points Allowed 35+    -> ESPN 35-45
}

#: Yahoo categories whose band edges do not line up with ESPN's.  Kept
#: separately so the League screen can say so instead of implying an exact
#: translation.
APPROXIMATE_STAT_IDS = {53, 54, 56}

#: Yahoo stat ids with no ESPN equivalent are offset into this range, which is
#: far above every real ESPN stat id (the highest is 159).
PRIVATE_STAT_BASE = 9000


def espn_stat_id(yahoo_stat_id: int) -> int:
    """The stat id this app should store a Yahoo category under."""
    return YAHOO_TO_ESPN_STAT.get(int(yahoo_stat_id), PRIVATE_STAT_BASE + int(yahoo_stat_id))


def is_private_stat(stat_id: int) -> bool:
    return int(stat_id) >= PRIVATE_STAT_BASE


#: Yahoo roster position -> the slot label the rest of the app uses.
YAHOO_SLOT_TO_LABEL = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "DEF": "DST",
    "D": "DST",
    "DST": "DST",
    "W/R": "RB/WR",
    "W/T": "WR/TE",
    "R/T": "RB/TE",
    "W/R/T": "FLEX",
    "Q/W/R/T": "OP",
    "BN": "BE",
    "IR": "IR",
    "IR+": "IR",
    "IL": "IR",
    "IL+": "IR",
    "NA": "IR",
}

#: Yahoo display positions -> our canonical player positions.
YAHOO_POSITION_TO_POSITION = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "PK": "K",
    "DEF": "DST",
    "DST": "DST",
    "D": "DST",
}


def normalise_yahoo_slot(raw: str | None) -> str:
    """Map a Yahoo roster position onto our slot vocabulary.

    Unknown slots (IDP positions in a defensive league) come back uppercased
    and are ignored downstream, exactly as unknown ESPN slots are.
    """
    if not raw:
        return ""
    value = str(raw).strip().upper()
    return YAHOO_SLOT_TO_LABEL.get(value, value)


def normalise_yahoo_position(raw: str | None) -> str:
    """Map a Yahoo position onto QB/RB/WR/TE/K/DST."""
    if not raw:
        return "UNKNOWN"
    value = str(raw).strip().upper().replace(" ", "")
    return YAHOO_POSITION_TO_POSITION.get(value, value)
