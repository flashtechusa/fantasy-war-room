"""Phase 7 -- draft simulation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from ..db import get_db
from ..engine.roster import build_optimal_lineup
from ..engine.simulate import DraftSimulator
from ..engine.valuation import ValuationEngine
from ..models import DraftSession, League
from ..services import season as season_service
from .deps import draft_session_dep, engine_dep, league_dep
from .serializers import serialize_simulation

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulate", tags=["simulation"])


class SimulationRequest(BaseModel):
    my_slot: int | None = Field(default=None, ge=1, le=32)
    simulations: int = Field(default=500, ge=10, le=20000)
    rounds: int | None = Field(default=None, ge=1, le=30)
    seed: int = Field(default=12345)


@router.post("")
def run_simulation(
    payload: SimulationRequest | None = None,
    engine: ValuationEngine = Depends(engine_dep),
    league: League = Depends(league_dep),
    draft: DraftSession = Depends(draft_session_dep),
    session: Session = Depends(get_db),
) -> dict:
    """Run mock drafts and derive a pre-draft strategy for this league.

    Opponents sample near their ADP rather than taking the next ranked player,
    so position runs and reaches show up the way they do in a real room.
    """
    payload = payload or SimulationRequest()
    slot = payload.my_slot or draft.my_draft_slot

    simulator = DraftSimulator(
        engine=engine,
        my_slot=slot,
        rounds=payload.rounds or draft.rounds,
        seed=payload.seed,
    )
    try:
        result = simulator.run(simulations=payload.simulations)
    except Exception as exc:  # noqa: BLE001
        log.exception("Simulation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Simulation failed: {exc}",
        ) from exc

    body = serialize_simulation(result)
    body["actual_league"] = _actual_league_points(session, league, engine)
    return body


def _actual_league_points(session, league: League, engine: ValuationEngine) -> dict:
    """What the real teams in this league actually scored, on the same scale.

    Without this the simulated percentiles have no reference class and mislead:
    the simulator's own weakest drafts read as "bad" even when they beat every
    real team in the league, because the label implies a human benchmark it
    does not have. The simulator plays a VOR-maximising strategy against
    opponents who only sample ADP, so it should be expected to land above real
    rosters -- showing both makes that visible instead of confusing.
    """
    rosters = season_service.rosters_by_team(session, league)
    totals: list[tuple[str, float, bool]] = []
    for team in league.teams:
        ids = rosters.get(team.espn_team_id) or set()
        if not ids:
            continue
        lineup = build_optimal_lineup(engine.roster_players(ids), engine.shape)
        totals.append((team.name, lineup.total_points, bool(team.is_mine)))

    if not totals:
        return {"available": False}

    points = sorted(t[1] for t in totals)
    best = max(totals, key=lambda t: t[1])
    mine = next((t for t in totals if t[2]), None)
    middle = len(points) // 2
    median = points[middle] if len(points) % 2 else (points[middle - 1] + points[middle]) / 2

    return {
        "available": True,
        "team_count": len(totals),
        "best": round(best[1], 1),
        "best_team": best[0],
        "median": round(median, 1),
        "worst": round(min(points), 1),
        "mine": round(mine[1], 1) if mine else None,
    }
