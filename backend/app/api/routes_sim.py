"""Phase 7 -- draft simulation."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..engine.simulate import DraftSimulator
from ..engine.valuation import ValuationEngine
from ..models import DraftSession, League
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

    return serialize_simulation(result)
