"""Score raw projected stat lines under *this* league's ESPN scoring rules.

Why not just use ESPN's projected point total?  Because ESPN's number bakes in
whatever settings it feels like, and because we want the same projection to be
re-scorable when a source other than ESPN supplies it.  Storing raw stat totals
and applying the league's rules ourselves is what makes the rankings genuinely
league-specific -- a 0.5-PPR league and a TD-heavy league get different boards
from the same projections.

ESPN models scoring as a flat list of `(statId, points)` pairs, plus optional
per-position `pointsOverrides`.  Bucketed categories (e.g. "300-399 yard passing
game") arrive as *counts* in the raw stat line, so a plain dot product over the
rules is the correct calculation, not an approximation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..espn.constants import stat_label

#: ESPN keys `pointsOverrides` by the *position* the override applies to.
POSITION_TO_OVERRIDE_KEY = {
    "QB": "0",
    "RB": "2",
    "WR": "4",
    "TE": "6",
    "DST": "16",
    "K": "17",
}


@dataclass(frozen=True)
class ScoringRule:
    stat_id: int
    abbrev: str
    label: str
    points: float
    points_overrides: dict[str, float]

    def points_for(self, position: str) -> float:
        key = POSITION_TO_OVERRIDE_KEY.get(position)
        if key and key in self.points_overrides:
            return self.points_overrides[key]
        return self.points


@dataclass
class ScoreBreakdownItem:
    stat_id: int
    abbrev: str
    label: str
    stat_value: float
    points_per: float
    points: float


class LeagueScoring:
    """Applies a league's scoring rules to raw stat lines."""

    def __init__(self, rules: list[ScoringRule]) -> None:
        # Ignore zero-point rules; ESPN sends hundreds of them.
        self.rules = [r for r in rules if r.points != 0.0 or r.points_overrides]
        self._by_stat_id = {r.stat_id: r for r in self.rules}

    @classmethod
    def from_dicts(cls, rules: list[dict]) -> "LeagueScoring":
        parsed = []
        for rule in rules:
            stat_id = int(rule["stat_id"])
            abbrev = rule.get("abbrev") or stat_label(stat_id)[0]
            label = rule.get("label") or stat_label(stat_id)[1]
            parsed.append(
                ScoringRule(
                    stat_id=stat_id,
                    abbrev=abbrev,
                    label=label,
                    points=float(rule.get("points") or 0.0),
                    points_overrides={
                        str(k): float(v) for k, v in (rule.get("points_overrides") or {}).items()
                    },
                )
            )
        return cls(parsed)

    # -- scoring -----------------------------------------------------------

    def score(self, raw_stats: dict[str, float] | dict[int, float], position: str = "") -> float:
        """Total fantasy points for a raw season stat line."""
        total = 0.0
        for key, value in (raw_stats or {}).items():
            rule = self._by_stat_id.get(int(key))
            if rule is None:
                continue
            total += float(value) * rule.points_for(position)
        return round(total, 2)

    def breakdown(
        self, raw_stats: dict[str, float] | dict[int, float], position: str = ""
    ) -> list[ScoreBreakdownItem]:
        """Per-category contribution, largest absolute contribution first.

        This is what the UI shows when you tap "how was this projection scored?".
        """
        items: list[ScoreBreakdownItem] = []
        for key, value in (raw_stats or {}).items():
            rule = self._by_stat_id.get(int(key))
            if rule is None:
                continue
            per = rule.points_for(position)
            points = float(value) * per
            if points == 0.0:
                continue
            items.append(
                ScoreBreakdownItem(
                    stat_id=rule.stat_id,
                    abbrev=rule.abbrev,
                    label=rule.label,
                    stat_value=round(float(value), 2),
                    points_per=per,
                    points=round(points, 2),
                )
            )
        items.sort(key=lambda i: abs(i.points), reverse=True)
        return items

    # -- introspection used by the League Settings screen -------------------

    @property
    def points_per_reception(self) -> float:
        for stat_id in (53, 41):
            rule = self._by_stat_id.get(stat_id)
            if rule is not None:
                return rule.points
        return 0.0

    @property
    def points_per_passing_td(self) -> float:
        rule = self._by_stat_id.get(4)
        return rule.points if rule else 0.0

    @property
    def format_label(self) -> str:
        ppr = self.points_per_reception
        if ppr >= 1.0:
            base = "Full PPR"
        elif ppr > 0:
            base = f"{ppr:g} PPR"
        else:
            base = "Standard"
        ptd = self.points_per_passing_td
        if ptd and ptd != 4.0:
            base += f", {ptd:g}pt passing TD"
        return base

    def active_rules(self) -> list[ScoringRule]:
        return sorted(self.rules, key=lambda r: r.stat_id)
