"""Normalised view of a league's roster construction.

Everything downstream (replacement level, roster need, the simulator) reasons
about the league through this object rather than poking at raw ESPN slot
labels.  It knows which slots are dedicated, which are multi-position, and how
many players the league actually drafts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..espn.constants import FANTASY_POSITIONS, MULTI_SLOT_ELIGIBILITY


@dataclass(frozen=True)
class FlexSlot:
    label: str
    count: int
    eligible: tuple[str, ...]


@dataclass
class LeagueShape:
    team_count: int = 10
    #: Dedicated single-position starting slots, e.g. {"QB": 1, "RB": 2}.
    dedicated: dict[str, int] = field(default_factory=dict)
    #: Multi-position starting slots (FLEX, superflex, RB/WR, ...).
    flex: list[FlexSlot] = field(default_factory=list)
    bench_slots: int = 6
    ir_slots: int = 1
    draft_type: str = "SNAKE"

    # -- construction ------------------------------------------------------

    @classmethod
    def from_slots(
        cls,
        team_count: int,
        roster_slots: dict[str, int],
        bench_slots: int = 6,
        ir_slots: int = 1,
        draft_type: str = "SNAKE",
    ) -> "LeagueShape":
        dedicated: dict[str, int] = {}
        flex: list[FlexSlot] = []
        for label, count in (roster_slots or {}).items():
            count = int(count or 0)
            if count <= 0:
                continue
            if label in FANTASY_POSITIONS:
                dedicated[label] = dedicated.get(label, 0) + count
            elif label in MULTI_SLOT_ELIGIBILITY:
                eligible = tuple(
                    p for p in MULTI_SLOT_ELIGIBILITY[label] if p in FANTASY_POSITIONS
                )
                if eligible:
                    flex.append(FlexSlot(label=label, count=count, eligible=eligible))
            # Unknown slots (IDP, HC, ...) are ignored by the offensive engine.
        return cls(
            team_count=max(int(team_count or 10), 2),
            dedicated=dedicated,
            flex=flex,
            bench_slots=max(int(bench_slots or 0), 0),
            ir_slots=max(int(ir_slots or 0), 0),
            draft_type=(draft_type or "SNAKE").upper(),
        )

    # -- derived numbers ---------------------------------------------------

    @property
    def flex_slot_count(self) -> int:
        return sum(f.count for f in self.flex)

    @property
    def starter_slots(self) -> int:
        return sum(self.dedicated.values()) + self.flex_slot_count

    @property
    def roster_size(self) -> int:
        """Players each team carries, excluding IR (IR spots aren't drafted)."""
        return self.starter_slots + self.bench_slots

    @property
    def draft_rounds(self) -> int:
        return self.roster_size

    @property
    def total_drafted(self) -> int:
        return self.roster_size * self.team_count

    @property
    def positions_in_play(self) -> list[str]:
        """Positions that can start in this league, in a stable display order."""
        active = set(self.dedicated)
        for slot in self.flex:
            active.update(slot.eligible)
        return [p for p in FANTASY_POSITIONS if p in active]

    def starters_at(self, position: str) -> int:
        """Dedicated starting slots for a position (excludes flex)."""
        return self.dedicated.get(position, 0)

    def flex_eligible(self, position: str) -> bool:
        return any(position in slot.eligible for slot in self.flex)

    def flex_slots_for(self, position: str) -> int:
        return sum(slot.count for slot in self.flex if position in slot.eligible)

    def is_superflex(self) -> bool:
        return any("QB" in slot.eligible for slot in self.flex)

    def league_starter_demand(self, position: str) -> int:
        """League-wide dedicated starters at a position."""
        return self.starters_at(position) * self.team_count

    def describe(self) -> str:
        parts = [f"{count}{pos}" for pos, count in self.dedicated.items() if count]
        for slot in self.flex:
            parts.append(f"{slot.count}{slot.label}")
        return f"{self.team_count}-team, " + " / ".join(parts) + f", {self.bench_slots} bench"
