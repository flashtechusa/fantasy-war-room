"""Phase 8 -- in-season decisions: start/sit, waivers, trades."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..engine.trades import analyse_trade
from ..engine.valuation import ValuationEngine
from ..engine.waivers import recommend_waivers
from ..engine.weekly import WeeklyPlayer, optimise_lineup
from ..espn.client import EspnConnectionError
from ..models import League
from ..services import season as season_service
from ..services.board import league_shape
from ..services.importer import import_free_agents
from ..services.provider import build_provider
from .deps import engine_dep, league_dep, settings_dep

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/season", tags=["season"])


def _serialize_player(player: WeeklyPlayer) -> dict:
    return {
        "espn_player_id": player.espn_player_id,
        "name": player.name,
        "position": player.position,
        "pro_team": player.pro_team,
        "week_points": player.week_points,
        "season_points": player.season_points,
        "vor": round(player.vor, 1),
        "bye_week": player.bye_week,
        "injury_status": player.injury_status,
        "percent_owned": player.percent_owned,
        "on_bye": player.on_bye,
        "week_projection_is_real": player.week_projection_is_real,
    }


def _resolve_week(week: int | None, settings: Settings) -> int:
    if week and week > 0:
        return week
    try:
        return build_provider(settings).current_week
    except Exception:      # noqa: BLE001 - a bad week is not worth a 500
        return 1


# ---------------------------------------------------------------------------
# Start / sit
# ---------------------------------------------------------------------------


@router.get("/lineup")
def start_sit(
    week: int | None = Query(None, ge=1, le=18),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Your best starting lineup for a week, and why each call was made."""
    week = _resolve_week(week, settings)
    roster_ids = season_service.my_roster_ids(session, league)
    if not roster_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No roster found. Import your league, and set FWR_MY_TEAM_ID (or your "
                "team name) so the app knows which team is yours."
            ),
        )

    roster = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=roster_ids
    )
    result = optimise_lineup(roster, league_shape(league), week)

    estimated = [p.name for p in roster if not p.week_projection_is_real]
    return {
        "week": week,
        "projected_points": result.projected_points,
        "points_vs_naive": result.points_vs_naive,
        "starters": [
            {
                "slot": decision.slot,
                "player": _serialize_player(decision.player) if decision.player else None,
                "next_best": (
                    _serialize_player(decision.next_best) if decision.next_best else None
                ),
                "margin": decision.margin,
                "close_call": decision.close_call,
                "warning": decision.warning,
                "reason": decision.reason,
            }
            for decision in result.starters
        ],
        "bench": [_serialize_player(p) for p in result.bench],
        "warnings": result.warnings,
        "close_calls": [
            {
                "slot": d.slot,
                "starting": d.player.name if d.player else None,
                "over": d.next_best.name if d.next_best else None,
                "margin": d.margin,
            }
            for d in result.close_calls
        ],
        "unfilled_slots": result.unfilled_slots,
        "estimated_projections": estimated,
    }


# ---------------------------------------------------------------------------
# Waivers
# ---------------------------------------------------------------------------


@router.post("/waivers/refresh")
def refresh_waivers(
    week: int | None = Query(None, ge=1, le=18),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Re-pull the free-agent pool from ESPN. Free agents move daily."""
    try:
        count = import_free_agents(
            session, league, build_provider(settings), settings, week=week
        )
    except EspnConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    from ..services import board as board_service

    board_service.clear_cache()
    return {"free_agents_imported": count}


@router.get("/waivers")
def waivers(
    week: int | None = Query(None, ge=1, le=18),
    limit: int = Query(15, ge=1, le=50),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Free agents ranked by what they'd actually add to your lineup."""
    week = _resolve_week(week, settings)
    roster_ids = season_service.my_roster_ids(session, league)
    roster = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=roster_ids
    )
    free_agents = season_service.build_weekly_players(
        session, league, engine, week,
        availability={"FREEAGENT", "WAIVERS"}, limit=180,
    )

    shape = league_shape(league)
    targets = recommend_waivers(
        roster=roster,
        free_agents=free_agents,
        shape=shape,
        week=week,
        faab_budget=league.acquisition_budget,
        faab_remaining=settings.faab_remaining,
        roster_is_full=len(roster) >= shape.roster_size,
        limit=limit,
    )

    return {
        "week": week,
        "roster_size": len(roster),
        "roster_is_full": len(roster) >= shape.roster_size,
        "free_agents_considered": len(free_agents),
        "uses_faab": league.uses_faab,
        "faab_budget": league.acquisition_budget,
        "faab_remaining": settings.faab_remaining,
        "targets": [
            {
                "player": _serialize_player(t.player),
                "week_gain": t.week_gain,
                "season_gain": t.season_gain,
                "drop": _serialize_player(t.drop) if t.drop else None,
                "priority": t.priority,
                "faab_bid": t.faab_bid,
                "faab_pct": t.faab_pct,
                "verdict": t.verdict,
                "reasons": t.reasons,
            }
            for t in targets
        ],
    }


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


class TradeRequest(BaseModel):
    give: list[int] = Field(default_factory=list, description="ESPN player ids you send")
    receive: list[int] = Field(default_factory=list, description="ESPN player ids you get")
    their_team_id: int | None = Field(
        default=None, description="Evaluate their side too, when known"
    )
    week: int | None = Field(default=None, ge=1, le=18)


@router.post("/trade")
def trade(
    payload: TradeRequest,
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """What a proposed trade does to both starting lineups."""
    if not payload.give and not payload.receive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pick at least one player to give or receive.",
        )

    week = _resolve_week(payload.week, settings)
    roster_ids = season_service.my_roster_ids(session, league)
    wanted = roster_ids | set(payload.give) | set(payload.receive)

    their_ids: set[int] = set()
    if payload.their_team_id:
        their_ids = season_service.team_roster_ids(league, payload.their_team_id)
        wanted |= their_ids

    everyone = {
        p.espn_player_id: p
        for p in season_service.build_weekly_players(
            session, league, engine, week, espn_player_ids=wanted
        )
    }

    missing = [pid for pid in payload.give + payload.receive if pid not in everyone]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown player id(s): {missing}. Refresh the player pool.",
        )

    my_roster = [everyone[pid] for pid in roster_ids if pid in everyone]
    give = [everyone[pid] for pid in payload.give]
    receive = [everyone[pid] for pid in payload.receive]
    their_roster = [everyone[pid] for pid in their_ids if pid in everyone] or None

    their_label = "Their team"
    if payload.their_team_id:
        match = next(
            (t for t in league.teams if t.espn_team_id == payload.their_team_id), None
        )
        if match:
            their_label = match.name

    result = analyse_trade(
        my_roster=my_roster,
        give=give,
        receive=receive,
        shape=league_shape(league),
        week=week,
        their_roster=their_roster,
        their_label=their_label,
    )

    def side(payload_side) -> dict | None:
        if payload_side is None:
            return None
        return {
            "label": payload_side.label,
            "gives": [_serialize_player(p) for p in payload_side.gives],
            "gets": [_serialize_player(p) for p in payload_side.gets],
            "week_before": payload_side.week_before,
            "week_after": payload_side.week_after,
            "season_before": payload_side.season_before,
            "season_after": payload_side.season_after,
            "week_delta": payload_side.week_delta,
            "season_delta": payload_side.season_delta,
            "roster_change": payload_side.roster_change,
            "position_changes": payload_side.position_changes,
            "notes": payload_side.notes,
        }

    return {
        "week": result.week,
        "verdict": result.verdict,
        "summary": result.summary,
        "reasons": result.reasons,
        "my_side": side(result.my_side),
        "their_side": side(result.their_side),
    }


@router.get("/roster")
def roster(
    week: int | None = Query(None, ge=1, le=18),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """My roster with this week's numbers -- the picker for the trade screen."""
    week = _resolve_week(week, settings)
    roster_ids = season_service.my_roster_ids(session, league)
    players = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=roster_ids
    )
    return {
        "week": week,
        "players": [_serialize_player(p) for p in players],
        "teams": [
            {"espn_team_id": t.espn_team_id, "name": t.name, "is_mine": t.is_mine}
            for t in league.teams
        ],
    }
