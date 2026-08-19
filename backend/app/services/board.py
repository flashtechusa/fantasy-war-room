"""Builds the valuation engine from what's in the database.

The engine is cached per league + data version so that a live draft, where the
board is rebuilt on every pick, does not re-derive replacement levels and tiers
hundreds of times.  Draft *state* is never cached -- only the league-invariant
part is.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.draft_math import DraftPosition, describe_position
from ..engine.league_shape import LeagueShape
from ..engine.scoring import LeagueScoring
from ..engine.valuation import BoardResult, PlayerInput, ValuationEngine
from ..models import League, Player, PlayerProjection, ProjectionSource


class LeagueNotImported(RuntimeError):
    """No league has been imported yet."""


@dataclass
class _CacheEntry:
    key: tuple
    engine: ValuationEngine


#: Keyed by league, because the engine is identical for everyone in a league --
#: the per-user parts (my roster, my picks) are computed outside it. A single
#: slot would thrash the moment two people in different leagues were signed in.
_cache: dict[tuple, _CacheEntry] = {}


def clear_cache() -> None:
    _cache.clear()


def league_shape(league: League) -> LeagueShape:
    return LeagueShape.from_slots(
        team_count=league.team_count,
        roster_slots=league.roster_slots or {},
        bench_slots=league.bench_slots,
        ir_slots=league.ir_slots,
        draft_type=league.draft_type,
    )


def league_scoring(league: League) -> LeagueScoring:
    return LeagueScoring.from_dicts(
        [
            {
                "stat_id": rule.stat_id,
                "abbrev": rule.abbrev,
                "label": rule.label,
                "points": rule.points,
                "points_overrides": rule.points_overrides or {},
            }
            for rule in league.scoring_rules
        ]
    )


def _blend_raw_stats(
    projections: list[PlayerProjection], weights: dict[str, float]
) -> tuple[dict[str, float], float]:
    """Weighted blend of every enabled source's raw stat line.

    With one source this is the identity.  With several it averages each stat
    id by source weight, which is what keeps the app from being hostage to a
    single projection provider.
    """
    usable = [
        (p, weights[p.source_key])
        for p in projections
        if p.source_key in weights and (p.raw_stats or p.source_points)
    ]
    if not usable:
        return ({}, 0.0)
    if len(usable) == 1:
        projection, _ = usable[0]
        return (
            {str(k): float(v) for k, v in (projection.raw_stats or {}).items()},
            float(projection.source_points or 0.0),
        )

    total_weight = sum(w for _p, w in usable) or 1.0
    blended: dict[str, float] = {}
    fallback_points = 0.0
    for projection, weight in usable:
        share = weight / total_weight
        for stat_id, value in (projection.raw_stats or {}).items():
            blended[str(stat_id)] = blended.get(str(stat_id), 0.0) + float(value) * share
        fallback_points += float(projection.source_points or 0.0) * share
    return ({k: round(v, 3) for k, v in blended.items()}, round(fallback_points, 2))


def build_engine(session: Session, league: League) -> ValuationEngine:
    """Cached ValuationEngine for a league."""
    latest_player = session.scalars(
        select(Player.updated_at)
        .where(Player.season == league.season, Player.source == league.source)
        .order_by(Player.updated_at.desc())
        .limit(1)
    ).first()
    key = (
        league.id,
        league.imported_at,
        latest_player,
        league.source,
        tuple(sorted((r.stat_id, r.points) for r in league.scoring_rules)),
    )
    cached = _cache.get(key)
    if cached is not None:
        return cached.engine

    weights = {
        source.key: source.weight
        for source in session.scalars(
            select(ProjectionSource).where(ProjectionSource.enabled.is_(True))
        ).all()
    }
    if not weights:
        weights = {"espn": 1.0, "demo": 1.0}

    # Scoped to the league's own provider. Real and synthetic players share one
    # season-keyed table, so without this a demo import puts fake players into
    # a real league's rankings -- which is exactly what happened: 330 of them
    # took over the top of every position, moved replacement level, and were
    # offered as free agents.
    players = session.scalars(
        select(Player).where(
            Player.season == league.season, Player.source == league.source
        )
    ).all()
    if not players:
        raise LeagueNotImported(
            "No players imported for this season. Run an import from League Settings."
        )

    by_player: dict[int, list[PlayerProjection]] = {}
    for projection in session.scalars(select(PlayerProjection)).all():
        by_player.setdefault(projection.player_id, []).append(projection)

    inputs: list[PlayerInput] = []
    for player in players:
        raw_stats, blended_points = _blend_raw_stats(by_player.get(player.id, []), weights)
        inputs.append(
            PlayerInput(
                espn_player_id=player.espn_player_id,
                name=player.name,
                position=player.position,
                pro_team=player.pro_team,
                bye_week=player.bye_week,
                adp=player.adp,
                espn_rank=player.espn_rank,
                espn_position_rank=player.espn_position_rank,
                espn_projected_points=blended_points or player.espn_projected_points,
                projected_games=player.projected_games,
                injury_status=player.injury_status,
                injured=player.injured,
                percent_owned=player.percent_owned,
                rookie=player.rookie,
                raw_stats=raw_stats,
            )
        )

    engine = ValuationEngine(
        scoring=league_scoring(league),
        shape=league_shape(league),
        players=inputs,
        source=league.source,
    )
    _cache[key] = _CacheEntry(key=key, engine=engine)
    # Bound it: a busy install should not accumulate an engine per league
    # forever, and rebuilding one is cheap next to serving a stale board.
    if len(_cache) > 16:
        for stale in list(_cache)[:-8]:
            _cache.pop(stale, None)
    return engine


def build_board(
    engine: ValuationEngine,
    league: League,
    drafted: list[dict] | None = None,
    my_player_ids: set[int] | None = None,
    my_slot: int = 1,
    current_pick: int | None = None,
    rounds: int | None = None,
) -> tuple[BoardResult, DraftPosition]:
    """Board plus draft-clock snapshot for a given draft state.

    `drafted` is [{"espn_player_id": .., "overall_pick": ..}] in pick order.
    """
    drafted = drafted or []
    shape = engine.shape
    rounds = rounds or shape.draft_rounds
    current_pick = current_pick or (len(drafted) + 1)

    position = describe_position(
        current_pick=current_pick,
        my_slot=my_slot,
        team_count=shape.team_count,
        rounds=rounds,
        draft_type=league.draft_type,
    )

    adp_by_id = {p.espn_player_id: p.adp for p in engine.players}
    drafted_with_picks = [
        (int(pick.get("overall_pick") or idx + 1), adp_by_id.get(pick["espn_player_id"]))
        for idx, pick in enumerate(drafted)
    ]

    return engine.build_board(
        drafted_ids={pick["espn_player_id"] for pick in drafted},
        my_player_ids=my_player_ids or set(),
        current_pick=current_pick,
        my_slot=my_slot,
        rounds=rounds,
        drafted_with_picks=drafted_with_picks,
        next_pick=position.my_next_pick,
        picks_until_next=position.picks_until_my_next,
        rounds_remaining=position.rounds_remaining,
    ), position
