"""Matching players across projection providers.

The hard part of adding a second source is not HTTP, it is that nobody agrees
on names. ESPN says "Kenneth Walker III", another site says "Kenneth Walker",
a third says "Ken Walker III". Team abbreviations differ too, and defences are
named half a dozen ways.

Getting this wrong is worse than not having the source at all: a mismatched
projection silently attaches one player's numbers to another, and nothing
downstream can detect it. So matching is deliberately conservative -- it
requires position agreement, prefers a team match, and refuses ambiguous cases
rather than guessing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Generational suffixes carry no identity -- providers disagree on whether to
#: include them for the same person.
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

#: Team abbreviations that differ between providers.
TEAM_ALIASES = {
    "JAC": "JAX",
    "WSH": "WAS",
    "WFT": "WAS",
    "LAR": "LA",
    "STL": "LA",
    "SD": "LAC",
    "OAK": "LV",
    "LVR": "LV",
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "SL": "LA",
    "KCC": "KC",
    "NEP": "NE",
    "NOS": "NO",
    "SFO": "SF",
    "TBB": "TB",
    "GBP": "GB",
}


def normalise_team(raw: str | None) -> str:
    if not raw:
        return ""
    value = re.sub(r"[^A-Z]", "", str(raw).upper())
    return TEAM_ALIASES.get(value, value)


def normalise_name(raw: str | None) -> str:
    """A comparable form of a player name.

    Strips accents, punctuation and generational suffixes, so "T.J. Hockenson",
    "TJ Hockenson" and "Tj  Hockenson" all collapse to the same key.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", str(raw))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    parts = [p for p in text.split() if p]
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def defence_key(raw: str | None) -> str:
    """Defences are named by city, nickname, or both, with varying suffixes."""
    name = normalise_name(raw)
    for noise in (" dst", " d st", " defense", " def", " special teams"):
        name = name.replace(noise, " ")
    return " ".join(name.split())


@dataclass(frozen=True)
class Candidate:
    """A player we could match against, from our own database."""

    player_id: int
    name: str
    position: str
    pro_team: str


class PlayerMatcher:
    """Resolves a provider's player onto one of ours, or refuses to.

    Refusing matters more than matching. An unmatched player costs us one
    projection; a wrong match corrupts two.
    """

    def __init__(self, candidates: list[Candidate]) -> None:
        self._by_name_team: dict[tuple[str, str, str], list[Candidate]] = {}
        self._by_name_pos: dict[tuple[str, str], list[Candidate]] = {}
        for candidate in candidates:
            key = normalise_name(candidate.name)
            if candidate.position == "DST":
                key = defence_key(candidate.name)
            team = normalise_team(candidate.pro_team)
            self._by_name_team.setdefault((key, candidate.position, team), []).append(candidate)
            self._by_name_pos.setdefault((key, candidate.position), []).append(candidate)

        self.unmatched: list[str] = []
        self.ambiguous: list[str] = []

    def match(self, name: str, position: str, pro_team: str | None) -> Candidate | None:
        key = defence_key(name) if position == "DST" else normalise_name(name)
        if not key:
            return None
        team = normalise_team(pro_team)

        # Name + position + team is the only unambiguous case.
        exact = self._by_name_team.get((key, position, team))
        if exact and len(exact) == 1:
            return exact[0]

        # Fall back to name + position. Players change teams mid-season and
        # providers update at different times, so a team mismatch alone is not
        # evidence of a different person.
        loose = self._by_name_pos.get((key, position))
        if loose and len(loose) == 1:
            return loose[0]
        if loose and len(loose) > 1:
            # Two players share a name and position. Without a team match there
            # is no way to tell them apart, so decline.
            self.ambiguous.append(f"{name} ({position})")
            return None

        self.unmatched.append(f"{name} ({position}, {pro_team or '?'})")
        return None

    @property
    def report(self) -> dict:
        return {
            "unmatched_count": len(self.unmatched),
            "ambiguous_count": len(self.ambiguous),
            "unmatched_sample": self.unmatched[:20],
            "ambiguous_sample": self.ambiguous[:20],
        }
