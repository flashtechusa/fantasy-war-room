"""Uneven-trade roster-move analysis: does a trade overflow a roster, and if so
who is the safest player to drop?

Pure and engine-agnostic. Given each side's current roster (as ``RosterPlayer``
objects, which already carry projection and value-over-replacement under the
active projection source), the players moving each way, and the league's active
roster limit, it reports the resulting size for *both* teams and, for the side
that overflows, ranks the safest drop candidates by their effect on the optimal
starting lineup and their value over replacement.

It never drops anyone -- it only measures and recommends. Turning a
recommendation into an actual roster move is a separate, explicitly confirmed
step, and encoding the drop into ESPN's transaction body is deferred until
ESPN's uneven-trade request format has been captured.
"""

from __future__ import annotations

from dataclasses import dataclass

from .league_shape import LeagueShape
from .roster import RosterPlayer, build_optimal_lineup


def active_roster_limit(roster_slots: dict | None, bench_slots: int | None) -> int:
    """Players a team may hold at once -- IR excluded.

    ESPN's trade roster cap is the active roster: every starting slot plus the
    bench. Injured-reserve slots sit outside it (that is why ESPN reports a
    maximum of 15 for a league whose full roster, IR included, is 17).
    """
    return int(sum((roster_slots or {}).values())) + int(bench_slots or 0)


@dataclass
class DropCandidate:
    espn_player_id: int
    name: str
    position: str
    projected_points: float
    vor: float
    #: Starts in the optimal lineup of the post-trade roster.
    is_starter: bool
    #: Points the optimal starting lineup loses if this player is dropped.
    lineup_impact: float
    #: Dropping this player leaves a required starting slot unfilled.
    creates_hole: bool


@dataclass
class TeamRosterMove:
    team_id: int
    team_name: str
    current_size: int
    resulting_size: int
    limit: int
    drops_required: int


@dataclass
class RosterMoveAnalysis:
    mine: TeamRosterMove
    theirs: TeamRosterMove
    #: My droppable keepers, safest to drop first.
    candidates: list[DropCandidate]
    #: A greedy pick of exactly ``mine.drops_required`` ids -- the recommendation.
    recommended_ids: list[int]


def analyse_roster_move(
    *,
    shape: LeagueShape,
    roster_limit: int,
    my_team_id: int,
    my_team_name: str,
    their_team_id: int,
    their_team_name: str,
    my_current: list[RosterPlayer],
    their_current: list[RosterPlayer],
    give: list[RosterPlayer],
    receive: list[RosterPlayer],
) -> RosterMoveAnalysis:
    """Resulting sizes for both teams, and safest drops for the side I control."""
    give_ids = {p.espn_player_id for p in give}

    my_result = len(my_current) - len(give) + len(receive)
    their_result = len(their_current) - len(receive) + len(give)

    mine = TeamRosterMove(
        team_id=my_team_id, team_name=my_team_name,
        current_size=len(my_current), resulting_size=my_result,
        limit=roster_limit, drops_required=max(0, my_result - roster_limit),
    )
    theirs = TeamRosterMove(
        team_id=their_team_id, team_name=their_team_name,
        current_size=len(their_current), resulting_size=their_result,
        limit=roster_limit, drops_required=max(0, their_result - roster_limit),
    )

    # Post-trade roster: my keepers (current minus what I send) plus what I get.
    # The players I receive are never drop candidates -- I want them -- and the
    # players I send are already leaving.
    keepers = [p for p in my_current if p.espn_player_id not in give_ids]
    post_trade = keepers + list(receive)

    base = build_optimal_lineup(post_trade, shape)
    starter_ids = {s.player.espn_player_id for s in base.starters if s.player}
    base_empty = len(base.empty_slots)

    candidates: list[DropCandidate] = []
    for p in keepers:
        without = [q for q in post_trade if q.espn_player_id != p.espn_player_id]
        lineup = build_optimal_lineup(without, shape)
        candidates.append(DropCandidate(
            espn_player_id=p.espn_player_id, name=p.name, position=p.position,
            projected_points=round(p.projected_points, 1), vor=round(p.vor, 1),
            is_starter=p.espn_player_id in starter_ids,
            lineup_impact=round(base.total_points - lineup.total_points, 2),
            creates_hole=len(lineup.empty_slots) > base_empty,
        ))

    # Safest first: never open a hole if avoidable, then don't disturb the
    # starting lineup, then least points lost, then least value over replacement.
    candidates.sort(key=lambda c: (c.creates_hole, c.is_starter, c.lineup_impact, c.vor))

    recommended = _recommend(shape, post_trade, keepers, mine.drops_required)
    return RosterMoveAnalysis(
        mine=mine, theirs=theirs, candidates=candidates, recommended_ids=recommended,
    )


def _recommend(
    shape: LeagueShape,
    post_trade: list[RosterPlayer],
    keepers: list[RosterPlayer],
    n: int,
) -> list[int]:
    """Greedy pick of ``n`` drops, re-evaluated after each so two cuts never both
    come out of a position that cannot spare them."""
    if n <= 0:
        return []
    droppable = {p.espn_player_id for p in keepers}
    working = list(post_trade)
    base = build_optimal_lineup(working, shape)
    chosen: list[int] = []
    for _ in range(n):
        best = None
        for p in working:
            if p.espn_player_id not in droppable or p.espn_player_id in chosen:
                continue
            without = [q for q in working if q.espn_player_id != p.espn_player_id]
            lineup = build_optimal_lineup(without, shape)
            key = (
                len(lineup.empty_slots) > len(base.empty_slots),  # avoid new hole
                round(base.total_points - lineup.total_points, 2),  # least impact
                p.vor,                                             # least value
            )
            if best is None or key < best[0]:
                best = (key, p.espn_player_id, without, lineup)
        if best is None:
            break
        _, pid, working, base = best
        chosen.append(pid)
    return chosen
