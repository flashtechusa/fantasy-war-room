"""The board: turns projections + league rules + draft state into a ranked,
explainable set of recommendations.

Nothing here is a black box.  Every component of the 0-100 Draft Score is kept
as its own number on the response, and every recommendation carries the plain
sentences that justify it.  If the engine says "take the RB", you can see that
it's because his VOR is second-best on the board, his tier drains in eight
picks, and the WR you'd be passing on will still be there in twelve.

Score components (weights in `SCORE_WEIGHTS`):
  production   raw projected points under our scoring rules
  vor          value over this league's replacement level
  scarcity     how fast this position's usable starters are disappearing
  availability how likely he is to be gone at our next pick
  roster_fit   marginal value he adds to our optimal starting lineup
  upside       realistic ceiling above the projection
  floor        downside protection (health, role security, workload)
  adp_value    how far he is falling relative to our own ranking
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..espn.constants import FANTASY_POSITIONS
from .availability import (
    AvailabilityModel,
    expected_best_available,
    measure_market_drift,
)
from .league_shape import LeagueShape
from .replacement import ReplacementLevels, compute_replacement_levels
from .roster import (
    PositionNeed,
    RosterPlayer,
    build_optimal_lineup,
    positional_needs,
)
from .scarcity import (
    PositionScarcity,
    TierAssignment,
    assign_tiers,
    normalise_scarcity,
    picks_until_tier_drains,
)
from .scoring import LeagueScoring

SCORE_WEIGHTS: dict[str, float] = {
    "production": 0.12,
    "vor": 0.24,
    "scarcity": 0.14,
    "availability": 0.13,
    "roster_fit": 0.11,
    "upside": 0.09,
    "floor": 0.09,
    "adp_value": 0.08,
}

#: Season-to-season spread of outcomes, by position.  These are priors used to
#: derive a ceiling/floor band around a point projection -- RB workloads swing
#: hardest, kickers barely move.  Tune in one place; surfaced in the UI.
POSITION_VOLATILITY = {
    "QB": 0.18,
    "RB": 0.30,
    "WR": 0.28,
    "TE": 0.26,
    "K": 0.15,
    "DST": 0.22,
}

INJURY_FLOOR_PENALTY = {
    "OUT": 0.35,
    "INJURY_RESERVE": 0.45,
    "SUSPENSION": 0.30,
    "DOUBTFUL": 0.20,
    "QUESTIONABLE": 0.08,
}

#: Kickers and defenses are worth almost nothing until the end of the draft.
LATE_ROUND_POSITIONS = {"K", "DST"}
LATE_ROUND_SUPPRESSION = 0.32

#: Restrict scaling to the meaningful part of the board so the long tail of
#: undraftable players doesn't compress everyone interesting into a narrow band.
SCALING_POOL_SIZE = 220


@dataclass
class PlayerInput:
    espn_player_id: int
    name: str
    position: str
    pro_team: str = "FA"
    bye_week: int | None = None
    adp: float | None = None
    espn_rank: int | None = None
    espn_position_rank: int | None = None
    espn_projected_points: float = 0.0
    projected_games: float = 17.0
    injury_status: str = "ACTIVE"
    injured: bool = False
    percent_owned: float = 0.0
    rookie: bool = False
    raw_stats: dict[str, float] = field(default_factory=dict)


@dataclass
class ScoreComponent:
    key: str
    label: str
    value: float  # 0-100 sub-score
    weight: float
    contribution: float
    detail: str = ""


@dataclass
class PlayerValuation:
    espn_player_id: int
    name: str
    position: str
    pro_team: str
    bye_week: int | None

    projected_points: float
    espn_projected_points: float
    points_per_game: float

    vor: float
    replacement_points: float
    position_rank: int
    overall_rank: int
    tier: int

    adp: float | None
    adp_delta: float  # positive => falling past our ranking (a value)
    espn_rank: int | None

    availability_next_pick: float  # probability he survives to our next pick
    gone_probability: float

    scarcity_score: float
    opportunity_cost: float
    wait_cost: float

    upside_points: float
    floor_points: float

    draft_score: float
    components: list[ScoreComponent]
    reasons: list[str]
    why_now: str
    why_wait: str
    principal_risk: str

    injury_status: str
    percent_owned: float
    projected_games: float
    is_drafted: bool = False
    drafted_by_me: bool = False
    value_grade: str = "fair"  # elite | good | fair | reach
    flags: list[str] = field(default_factory=list)


@dataclass
class BoardResult:
    valuations: list[PlayerValuation]
    replacement: ReplacementLevels
    scarcity: dict[str, PositionScarcity]
    needs: dict[str, PositionNeed]
    market_drift: float
    current_pick: int
    next_pick: int | None
    picks_until_next: int | None
    position_decay: dict[str, float]
    scoring_format: str
    league_shape: str
    generated_from: str = "espn"

    def top(self, n: int = 10) -> list[PlayerValuation]:
        return [v for v in self.valuations if not v.is_drafted][:n]


class _Scaler:
    """Clamp-and-stretch to 0-100 over the meaningful part of the distribution."""

    def __init__(self, values: list[float], low_pct: float = 0.05, high_pct: float = 0.99):
        clean = sorted(v for v in values if v is not None)
        if not clean:
            self.lo, self.hi = 0.0, 1.0
            return
        lo_idx = min(int(len(clean) * low_pct), len(clean) - 1)
        hi_idx = min(int(len(clean) * high_pct), len(clean) - 1)
        self.lo = clean[lo_idx]
        self.hi = clean[hi_idx]
        if self.hi - self.lo < 1e-9:
            self.hi = self.lo + 1.0

    def __call__(self, value: float | None) -> float:
        if value is None:
            return 0.0
        scaled = 100.0 * (value - self.lo) / (self.hi - self.lo)
        return round(max(0.0, min(100.0, scaled)), 1)


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class ValuationEngine:
    """Builds the board for one league, one draft state."""

    def __init__(
        self,
        scoring: LeagueScoring,
        shape: LeagueShape,
        players: list[PlayerInput],
        source: str = "espn",
    ) -> None:
        self.scoring = scoring
        self.shape = shape
        self.players = [p for p in players if p.position in FANTASY_POSITIONS]
        self.source = source

        self._points: dict[int, float] = {}
        for player in self.players:
            self._points[player.espn_player_id] = self._project(player)

        self._by_position: dict[str, list[PlayerInput]] = {}
        for position in FANTASY_POSITIONS:
            group = [p for p in self.players if p.position == position]
            group.sort(key=lambda p: self._points[p.espn_player_id], reverse=True)
            self._by_position[position] = group

        # Replacement level is a property of the league's structure, so it is
        # computed once over the whole pool and does NOT drift as players are
        # taken -- a drafted stud shouldn't change what "replacement" means.
        self.replacement = compute_replacement_levels(
            {
                pos: [self._points[p.espn_player_id] for p in group]
                for pos, group in self._by_position.items()
            },
            shape,
        )

        self._tiers: dict[str, TierAssignment] = {
            pos: assign_tiers([(p.espn_player_id, self._points[p.espn_player_id]) for p in group])
            for pos, group in self._by_position.items()
        }

        self._position_rank: dict[int, int] = {}
        for group in self._by_position.values():
            for idx, player in enumerate(group, start=1):
                self._position_rank[player.espn_player_id] = idx

        self._vor: dict[int, float] = {
            p.espn_player_id: self.replacement.vor(p.position, self._points[p.espn_player_id])
            for p in self.players
        }

        self._our_rank: dict[int, int] = {}
        for idx, player in enumerate(
            sorted(self.players, key=lambda p: self._vor[p.espn_player_id], reverse=True), start=1
        ):
            self._our_rank[player.espn_player_id] = idx

    # -- projections -------------------------------------------------------

    def _project(self, player: PlayerInput) -> float:
        """Score the raw stat line under our league's rules.

        Falls back to ESPN's own applied total when a source gives us no raw
        stats (so a player is never silently valued at zero).
        """
        if player.raw_stats:
            points = self.scoring.score(player.raw_stats, player.position)
            if points != 0.0:
                return points
        return round(player.espn_projected_points, 2)

    def points_for(self, player_id: int) -> float:
        return self._points.get(player_id, 0.0)

    # -- upside / floor ----------------------------------------------------

    def _upside_floor(self, player: PlayerInput, points: float) -> tuple[float, float]:
        """Heuristic ceiling/floor band around the point projection.

        These are *priors*, not simulated distributions -- position volatility,
        health, workload security and market mispricing.  They are labelled as
        such in the UI so they aren't mistaken for a projection source.
        """
        volatility = POSITION_VOLATILITY.get(player.position, 0.25)

        injury_hit = INJURY_FLOOR_PENALTY.get((player.injury_status or "").upper(), 0.0)
        games_hit = max(0.0, (17.0 - player.projected_games) / 17.0)
        # Widely rostered players have secure roles; deep-league fliers do not.
        role_security = min(max(player.percent_owned, 0.0), 100.0) / 100.0

        floor_factor = 1.0 - volatility * (1.0 - 0.35 * role_security) - injury_hit - games_hit * 0.5
        floor_factor = max(0.15, min(1.0, floor_factor))

        upside_bonus = 0.0
        if player.rookie:
            upside_bonus += 0.06
        # Underpriced by the market => more room to beat the projection.
        our_rank = self._our_rank.get(player.espn_player_id, 999)
        if player.adp and player.adp > our_rank + 12:
            upside_bonus += 0.05
        if role_security < 0.35:
            upside_bonus += 0.04  # lottery ticket

        upside_factor = 1.0 + volatility * (1.0 + upside_bonus * 4)
        return (round(points * upside_factor, 1), round(points * floor_factor, 1))

    # -- the board ---------------------------------------------------------

    def build_board(
        self,
        drafted_ids: set[int] | None = None,
        my_player_ids: set[int] | None = None,
        current_pick: int = 1,
        my_slot: int = 1,
        rounds: int | None = None,
        drafted_with_picks: list[tuple[int, float | None]] | None = None,
        next_pick: int | None = None,
        picks_until_next: int | None = None,
        rounds_remaining: int | None = None,
    ) -> BoardResult:
        drafted_ids = set(drafted_ids or set())
        my_player_ids = set(my_player_ids or set())
        rounds = rounds or self.shape.draft_rounds

        drift = measure_market_drift(drafted_with_picks or [])
        availability = AvailabilityModel(
            current_pick=current_pick, next_pick=next_pick, market_drift=drift
        )

        undrafted = [p for p in self.players if p.espn_player_id not in drafted_ids]
        undrafted.sort(key=lambda p: self._vor[p.espn_player_id], reverse=True)

        # -- my roster, for marginal value --------------------------------
        my_roster = [
            RosterPlayer(
                espn_player_id=p.espn_player_id,
                name=p.name,
                position=p.position,
                projected_points=self._points[p.espn_player_id],
                bye_week=p.bye_week,
                pro_team=p.pro_team,
                vor=self._vor[p.espn_player_id],
                injury_status=p.injury_status,
            )
            for p in self.players
            if p.espn_player_id in my_player_ids
        ]

        best_available_points: dict[str, float] = {}
        for position in self.shape.positions_in_play:
            group = [p for p in undrafted if p.position == position]
            if group:
                best_available_points[position] = max(
                    self._points[p.espn_player_id] for p in group
                )

        needs = positional_needs(
            my_roster,
            self.shape,
            best_available_points,
            replacement_points={
                position: baseline.replacement_points
                for position, baseline in self.replacement.positions.items()
            },
            rounds_remaining=rounds_remaining,
        )

        # -- positional scarcity & decay ----------------------------------
        scarcity, decay = self._position_scarcity(undrafted, availability, needs, current_pick)

        # -- scalers over the relevant slice of the board -------------------
        pool = undrafted[:SCALING_POOL_SIZE] or undrafted
        pool_ids = [p.espn_player_id for p in pool]
        vor_scaler = _Scaler([self._vor[i] for i in pool_ids])
        points_scaler = _Scaler([self._points[i] for i in pool_ids])

        upside_floor = {
            p.espn_player_id: self._upside_floor(p, self._points[p.espn_player_id])
            for p in self.players
        }
        upside_scaler = _Scaler([upside_floor[i][0] for i in pool_ids])
        floor_scaler = _Scaler([floor for _up, floor in (upside_floor[i] for i in pool_ids)])

        adp_deltas = {}
        for player in self.players:
            our_rank = self._our_rank[player.espn_player_id]
            adp_deltas[player.espn_player_id] = (
                round(player.adp - our_rank, 1) if player.adp else 0.0
            )
        adp_scaler = _Scaler([adp_deltas[i] for i in pool_ids], low_pct=0.10, high_pct=0.90)

        # -- valuations -----------------------------------------------------
        valuations: list[PlayerValuation] = []
        for player in self.players:
            pid = player.espn_player_id
            valuations.append(
                self._value_player(
                    player=player,
                    availability=availability,
                    needs=needs,
                    scarcity=scarcity,
                    decay=decay,
                    scalers=(vor_scaler, points_scaler, upside_scaler, floor_scaler, adp_scaler),
                    upside_floor=upside_floor[pid],
                    adp_delta=adp_deltas[pid],
                    is_drafted=pid in drafted_ids,
                    drafted_by_me=pid in my_player_ids,
                    rounds_remaining=rounds_remaining,
                    undrafted=undrafted,
                )
            )

        valuations.sort(key=lambda v: (v.is_drafted, -v.draft_score))
        for idx, valuation in enumerate(
            [v for v in valuations if not v.is_drafted], start=1
        ):
            valuation.overall_rank = idx

        return BoardResult(
            valuations=valuations,
            replacement=self.replacement,
            scarcity=scarcity,
            needs=needs,
            market_drift=round(drift, 2),
            current_pick=current_pick,
            next_pick=next_pick,
            picks_until_next=picks_until_next,
            position_decay={k: round(v, 1) for k, v in decay.items()},
            scoring_format=self.scoring.format_label,
            league_shape=self.shape.describe(),
            generated_from=self.source,
        )

    # -- scarcity ----------------------------------------------------------

    def _position_scarcity(
        self,
        undrafted: list[PlayerInput],
        availability: AvailabilityModel,
        needs: dict[str, PositionNeed],
        current_pick: int,
    ) -> tuple[dict[str, PositionScarcity], dict[str, float]]:
        raw_scarcity: dict[str, float] = {}
        decay: dict[str, float] = {}
        interim: dict[str, dict] = {}

        for position in self.shape.positions_in_play:
            group = [p for p in undrafted if p.position == position]
            if not group:
                continue
            group.sort(key=lambda p: self._points[p.espn_player_id], reverse=True)

            replacement = self.replacement.replacement_for(position)
            starters_left = sum(
                1 for p in group if self._points[p.espn_player_id] > replacement
            )

            best_now_points = self._points[group[0].espn_player_id]
            top_tier = self._tiers[position].tier_of(group[0].espn_player_id)
            tier_members = [
                p for p in group if self._tiers[position].tier_of(p.espn_player_id) == top_tier
            ]
            below_tier = [
                p for p in group if self._tiers[position].tier_of(p.espn_player_id) != top_tier
            ]
            cliff = (
                round(
                    self._points[tier_members[-1].espn_player_id]
                    - self._points[below_tier[0].espn_player_id],
                    1,
                )
                if tier_members and below_tier
                else 0.0
            )

            picks_to_cliff = picks_until_tier_drains(
                [availability.effective_adp(p.adp, self._our_rank.get(p.espn_player_id))
                 for p in tier_members],
                current_pick,
                availability,
            )

            expected_next = expected_best_available(
                [
                    (
                        self._points[p.espn_player_id],
                        availability.probability_available_next(
                            p.adp, self._our_rank.get(p.espn_player_id)
                        ),
                    )
                    for p in group[:40]
                ]
            )
            dropoff = round(max(0.0, best_now_points - expected_next), 1)
            # Value bleeding away only matters at positions we still need, but
            # never zero it out entirely -- needs change as the draft moves.
            need_weight = needs[position].need_score if position in needs else 0.0
            decay[position] = dropoff * max(need_weight, 0.15)

            # Raw scarcity blends "value bleeding away" with "how few are left".
            scarcity_raw = dropoff + (cliff * 0.35 if picks_to_cliff is not None else 0.0)
            if starters_left <= self.shape.team_count * 0.5:
                scarcity_raw *= 1.25
            raw_scarcity[position] = scarcity_raw

            interim[position] = {
                "starters_left": starters_left,
                "tier_players_left": len(tier_members),
                "cliff_points": cliff,
                "picks_to_cliff": picks_to_cliff,
                "dropoff": dropoff,
                "expected_best_next": round(expected_next, 1),
                "best_available_now": round(best_now_points, 1),
            }

        normalised = normalise_scarcity(raw_scarcity)
        scarcity = {
            position: PositionScarcity(
                position=position,
                starters_left=data["starters_left"],
                tier_players_left=data["tier_players_left"],
                cliff_points=data["cliff_points"],
                picks_to_cliff=data["picks_to_cliff"],
                dropoff_to_next_pick=data["dropoff"],
                scarcity_score=normalised.get(position, 50.0),
                expected_best_next=data["expected_best_next"],
                best_available_now=data["best_available_now"],
            )
            for position, data in interim.items()
        }
        return scarcity, decay

    # -- one player --------------------------------------------------------

    def _value_player(
        self,
        player: PlayerInput,
        availability: AvailabilityModel,
        needs: dict[str, PositionNeed],
        scarcity: dict[str, PositionScarcity],
        decay: dict[str, float],
        scalers: tuple[_Scaler, _Scaler, _Scaler, _Scaler, _Scaler],
        upside_floor: tuple[float, float],
        adp_delta: float,
        is_drafted: bool,
        drafted_by_me: bool,
        rounds_remaining: int | None,
        undrafted: list[PlayerInput],
    ) -> PlayerValuation:
        vor_scaler, points_scaler, upside_scaler, floor_scaler, adp_scaler = scalers
        pid = player.espn_player_id
        points = self._points[pid]
        vor = self._vor[pid]
        our_rank = self._our_rank[pid]

        prob_available = availability.probability_available_next(player.adp, our_rank)
        gone = round(1.0 - prob_available, 4)

        position_scarcity = scarcity.get(player.position)
        need = needs.get(player.position)
        upside_points, floor_points = upside_floor

        # --- components ----------------------------------------------------
        components = [
            ScoreComponent(
                key="vor",
                label="Value over replacement",
                value=vor_scaler(vor),
                weight=SCORE_WEIGHTS["vor"],
                contribution=0.0,
                detail=(
                    f"{vor:+.1f} pts vs the {player.position} replacement level "
                    f"({self.replacement.replacement_for(player.position):.1f})"
                ),
            ),
            ScoreComponent(
                key="production",
                label="Projected production",
                value=points_scaler(points),
                weight=SCORE_WEIGHTS["production"],
                contribution=0.0,
                detail=f"{points:.1f} projected pts under your scoring rules",
            ),
            ScoreComponent(
                key="scarcity",
                label="Positional scarcity",
                value=position_scarcity.scarcity_score if position_scarcity else 50.0,
                weight=SCORE_WEIGHTS["scarcity"],
                contribution=0.0,
                detail=position_scarcity.explain() if position_scarcity else "",
            ),
            ScoreComponent(
                key="availability",
                label="Gone by next pick",
                value=round(gone * 100, 1),
                weight=SCORE_WEIGHTS["availability"],
                contribution=0.0,
                detail=(
                    f"{_pct(gone)} chance he is gone before your next pick"
                    if availability.next_pick
                    else "No further picks to protect against"
                ),
            ),
            ScoreComponent(
                key="roster_fit",
                label="Roster fit",
                value=round((need.need_score if need else 0.0) * 100, 1),
                weight=SCORE_WEIGHTS["roster_fit"],
                contribution=0.0,
                detail=need.note if need else "",
            ),
            ScoreComponent(
                key="upside",
                label="Upside",
                value=upside_scaler(upside_points),
                weight=SCORE_WEIGHTS["upside"],
                contribution=0.0,
                detail=f"Ceiling band ~{upside_points:.0f} pts",
            ),
            ScoreComponent(
                key="floor",
                label="Floor",
                value=floor_scaler(floor_points),
                weight=SCORE_WEIGHTS["floor"],
                contribution=0.0,
                detail=f"Downside band ~{floor_points:.0f} pts",
            ),
            ScoreComponent(
                key="adp_value",
                label="ADP value",
                value=adp_scaler(adp_delta),
                weight=SCORE_WEIGHTS["adp_value"],
                contribution=0.0,
                detail=(
                    f"ADP {player.adp:.0f} vs our rank {our_rank} ({adp_delta:+.0f})"
                    if player.adp
                    else "No ESPN ADP available"
                ),
            ),
        ]
        for component in components:
            component.contribution = round(component.value * component.weight, 2)

        score = sum(c.contribution for c in components)

        # --- guardrails ----------------------------------------------------
        flags: list[str] = []
        if player.position in LATE_ROUND_POSITIONS:
            slots_needed = self.shape.starters_at(player.position)
            if (
                rounds_remaining is None
                or rounds_remaining > slots_needed + 1
            ):
                score *= LATE_ROUND_SUPPRESSION
                flags.append("late-round position")

        injury = (player.injury_status or "").upper()
        if injury in {"OUT", "INJURY_RESERVE", "SUSPENSION"}:
            score *= 0.75
            flags.append("injury risk")
        elif injury == "DOUBTFUL":
            score *= 0.9
            flags.append("injury risk")

        score = round(max(0.0, min(100.0, score)), 1)

        # Surfaced on the board as its own icon: a startable player the market
        # is very likely to take before we pick again.
        if vor > 0 and gone >= 0.65 and availability.next_pick:
            flags.append("disappearing")

        # --- narrative ------------------------------------------------------
        # What we give up elsewhere by spending this pick here.  Kickers and
        # defenses are excluded -- passing on them costs nothing.
        opportunity_cost = round(
            max(
                (
                    v
                    for pos, v in decay.items()
                    if pos != player.position and pos not in LATE_ROUND_POSITIONS
                ),
                default=0.0,
            ),
            1,
        )
        wait_cost = round(decay.get(player.position, 0.0), 1)

        reasons, why_now, why_wait, risk = self._explain(
            player=player,
            points=points,
            vor=vor,
            our_rank=our_rank,
            gone=gone,
            scarcity=position_scarcity,
            need=need,
            decay=decay,
            adp_delta=adp_delta,
            opportunity_cost=opportunity_cost,
            wait_cost=wait_cost,
            upside_points=upside_points,
            floor_points=floor_points,
            undrafted=undrafted,
            availability=availability,
            flags=flags,
        )

        value_grade = self._grade(adp_delta, vor, gone)

        return PlayerValuation(
            espn_player_id=pid,
            name=player.name,
            position=player.position,
            pro_team=player.pro_team,
            bye_week=player.bye_week,
            projected_points=round(points, 1),
            espn_projected_points=round(player.espn_projected_points, 1),
            points_per_game=round(points / max(player.projected_games, 1.0), 2),
            vor=round(vor, 1),
            replacement_points=round(self.replacement.replacement_for(player.position), 1),
            position_rank=self._position_rank[pid],
            overall_rank=our_rank,
            tier=self._tiers[player.position].tier_of(pid),
            adp=player.adp,
            adp_delta=adp_delta,
            espn_rank=player.espn_rank,
            availability_next_pick=round(prob_available, 4),
            gone_probability=gone,
            scarcity_score=position_scarcity.scarcity_score if position_scarcity else 50.0,
            opportunity_cost=opportunity_cost,
            wait_cost=wait_cost,
            upside_points=upside_points,
            floor_points=floor_points,
            draft_score=score,
            components=components,
            reasons=reasons,
            why_now=why_now,
            why_wait=why_wait,
            principal_risk=risk,
            injury_status=player.injury_status,
            percent_owned=player.percent_owned,
            projected_games=player.projected_games,
            is_drafted=is_drafted,
            drafted_by_me=drafted_by_me,
            value_grade=value_grade,
            flags=flags,
        )

    @staticmethod
    def _grade(adp_delta: float, vor: float, gone: float) -> str:
        """Value-vs-cost grade. Always paired with a text label in the UI.

        Pre-draft, when our ranking and ADP agree, most players are correctly
        "fair" -- the grades earn their keep once players start falling.
        """
        if vor <= 0:
            return "reach" if adp_delta < -10 else "fair"
        if adp_delta >= 10:
            return "elite"
        if adp_delta >= 3:
            return "good"
        if adp_delta <= -12:
            return "reach"
        return "fair"

    def _explain(
        self,
        player: PlayerInput,
        points: float,
        vor: float,
        our_rank: int,
        gone: float,
        scarcity: PositionScarcity | None,
        need: PositionNeed | None,
        decay: dict[str, float],
        adp_delta: float,
        opportunity_cost: float,
        wait_cost: float,
        upside_points: float,
        floor_points: float,
        undrafted: list[PlayerInput],
        availability: AvailabilityModel,
        flags: list[str],
    ) -> tuple[list[str], str, str, str]:
        """Human-readable justification. This is the whole point of the app."""
        reasons: list[str] = []

        # 1. Where he sits on the board.
        remaining_rank = 1 + sum(
            1 for p in undrafted if self._vor[p.espn_player_id] > vor
        )
        reasons.append(
            f"{_ordinal(remaining_rank)} remaining player by VOR "
            f"({vor:+.0f} vs {player.position} replacement)"
        )

        # 2. What the position is doing.
        if scarcity and scarcity.cliff_points >= 8 and scarcity.picks_to_cliff is not None:
            if scarcity.tier_players_left <= 1:
                reasons.append(
                    f"Last player in the top {player.position} tier -- "
                    f"a {scarcity.cliff_points:.0f}-pt cliff sits right behind him"
                )
            else:
                reasons.append(
                    f"{player.position} tier projected to drop sharply within "
                    f"{scarcity.picks_to_cliff} picks "
                    f"({scarcity.cliff_points:.0f}-pt cliff, "
                    f"{scarcity.tier_players_left} left in the tier)"
                )
        elif scarcity and scarcity.starters_left <= self.shape.team_count:
            reasons.append(
                f"Only {scarcity.starters_left} startable {player.position}s remain"
            )

        # 3. Urgency.
        if availability.next_pick:
            if gone >= 0.6:
                reasons.append(
                    f"{_pct(gone)} probability he is unavailable at our next selection "
                    f"(pick {availability.next_pick})"
                )
            elif gone <= 0.25:
                reasons.append(
                    f"Likely to survive to pick {availability.next_pick} "
                    f"({_pct(1 - gone)} available)"
                )

        # 4. Comparative depth across positions.  Only compare against positions
        # we'd realistically draft here -- "deeper than kicker" is not insight.
        deeper = [
            pos
            for pos, value in sorted(decay.items(), key=lambda kv: kv[1])
            if pos != player.position
            and pos not in LATE_ROUND_POSITIONS
            and value < wait_cost - 5
        ]
        if deeper:
            reasons.append(
                f"{'/'.join(deeper[:2])} alternatives are substantially deeper "
                f"({wait_cost:.0f} pts lost waiting on {player.position} vs "
                f"{decay.get(deeper[0], 0):.0f} at {deeper[0]})"
            )

        # 5. Roster construction.
        if need:
            if need.starters_filled < need.starters_required:
                slot_label = f"{player.position}{need.starters_filled + 1}"
                if opportunity_cost <= wait_cost:
                    reasons.append(
                        f"Fills {slot_label} without creating major opportunity cost"
                    )
                else:
                    reasons.append(
                        f"Fills {slot_label}, but costs ~{opportunity_cost:.0f} pts "
                        f"of value elsewhere"
                    )
            elif need.marginal_value > 0:
                reasons.append(
                    f"Upgrades the lineup by {need.marginal_value:.0f} pts even though "
                    f"{player.position} is already started"
                )

        # 6. Market value.
        if adp_delta >= 10:
            reasons.append(
                f"Falling {adp_delta:.0f} picks past our own ranking (ADP {player.adp:.0f})"
            )
        elif adp_delta <= -12:
            reasons.append(
                f"Would be a {abs(adp_delta):.0f}-pick reach against ADP {player.adp:.0f}"
            )

        if "late-round position" in flags:
            reasons.append(
                f"{player.position} is heavily discounted until the final rounds -- "
                "the drop-off from the best to a streamer is small"
            )

        # -- why now / why wait / risk ---------------------------------------
        if gone >= 0.6 and vor > 0:
            why_now = (
                f"He is {_pct(gone)} likely to be gone at pick {availability.next_pick}, "
                f"and the next {player.position} costs you about "
                f"{scarcity.dropoff_to_next_pick:.0f} pts." if scarcity else
                f"He is {_pct(gone)} likely to be gone at your next pick."
            )
        elif vor > 0 and wait_cost >= opportunity_cost:
            why_now = (
                f"Best value on the board and {player.position} is the position "
                f"bleeding the most value ({wait_cost:.0f} pts by your next pick)."
            )
        else:
            why_now = (
                f"Solid value at {vor:+.0f} VOR with a {floor_points:.0f}-pt floor."
            )

        if gone <= 0.35:
            why_wait = (
                f"{_pct(1 - gone)} chance he is still there at pick "
                f"{availability.next_pick} -- you can take a scarcer position first."
                if availability.next_pick
                else "No further picks, so there is nothing to wait for."
            )
        elif opportunity_cost > wait_cost:
            drafting_decay = {
                pos: v for pos, v in decay.items() if pos not in LATE_ROUND_POSITIONS
            }
            leader = (
                max(drafting_decay.items(), key=lambda kv: kv[1])[0]
                if drafting_decay
                else "another position"
            )
            why_wait = (
                f"{leader} is losing more value right now "
                f"({opportunity_cost:.0f} pts vs {wait_cost:.0f} here)."
            )
        else:
            why_wait = (
                f"Only if you are comfortable losing him -- {_pct(gone)} chance he's gone."
            )

        if (player.injury_status or "ACTIVE").upper() not in {"ACTIVE", "NORMAL", ""}:
            risk = f"Injury designation: {player.injury_status.title()}."
        elif player.projected_games < 15:
            risk = f"Projected for only {player.projected_games:.0f} games."
        elif floor_points < self.replacement.replacement_for(player.position):
            risk = (
                f"Floor ({floor_points:.0f} pts) is below replacement level -- "
                "if the role doesn't hold, this pick returns nothing."
            )
        elif adp_delta <= -12:
            risk = (
                f"You are reaching {abs(adp_delta):.0f} picks ahead of the market; "
                "he would likely be there next round."
            )
        elif player.rookie:
            risk = "Rookie -- wider outcome range than the projection implies."
        else:
            risk = (
                f"Standard downside: a {POSITION_VOLATILITY.get(player.position, 0.25):.0%} "
                f"swing at {player.position} puts him near {floor_points:.0f} pts."
            )

        return (reasons, why_now, why_wait, risk)

    # -- helpers used by My Team / simulator -------------------------------

    def average_starter_points(self) -> dict[str, float]:
        """Mean projection of a typical starter at each position in this league."""
        out: dict[str, float] = {}
        for position, group in self._by_position.items():
            demand = self.replacement.positions.get(position)
            slots = self.shape.league_starter_demand(position)
            if demand is not None:
                slots = max(slots + demand.flex_demand, 1)
            slots = max(slots, 1)
            top = [self._points[p.espn_player_id] for p in group[:slots]]
            if top:
                out[position] = round(sum(top) / len(top), 1)
        return out

    def roster_players(self, player_ids: set[int]) -> list[RosterPlayer]:
        return [
            RosterPlayer(
                espn_player_id=p.espn_player_id,
                name=p.name,
                position=p.position,
                projected_points=self._points[p.espn_player_id],
                bye_week=p.bye_week,
                pro_team=p.pro_team,
                vor=self._vor[p.espn_player_id],
                injury_status=p.injury_status,
            )
            for p in self.players
            if p.espn_player_id in player_ids
        ]

    def optimal_lineup(self, player_ids: set[int]):
        return build_optimal_lineup(self.roster_players(player_ids), self.shape)
