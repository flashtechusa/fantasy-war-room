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
from ..engine.waivers import explain_by_position, recommend_waivers
from ..engine.weekly import WeeklyPlayer, optimise_lineup
from ..espn import trade_write
from ..espn.client import EspnConnectionError, EspnNotConfigured
from ..models import League
from ..services import season as season_service
from ..services.board import league_shape
from ..services.importer import import_free_agents
from ..services.provider import build_provider
from .deps import engine_dep, league_dep, settings_dep
from .routes_auth import require_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/season", tags=["season"])

#: How deep down each position's wire to look. Per position rather than
#: overall, so a position with small point totals is never crowded out.
WIRE_DEPTH_PER_POSITION = 40


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
    # ESPN publishes real per-week splits only a short way ahead. Past that the
    # fallback is the season total spread evenly, which is the *same number
    # every week* -- so consecutive future weeks look identical and the screen
    # reads as broken. Say which case we are in rather than leaving the reader
    # to infer it from a list of names.
    if not roster:
        projection_basis = "none"
    elif not estimated:
        projection_basis = "espn_weekly"
    elif len(estimated) == len(roster):
        projection_basis = "season_average"
    else:
        projection_basis = "mixed"

    return {
        "week": week,
        "projection_basis": projection_basis,
        "estimated_count": len(estimated),
        "roster_count": len(roster),
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
    except EspnNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
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
    # Per position, not a flat cut. Sorting the whole wire by projected points
    # and keeping the top 180 is position-blind: quarterbacks project three
    # times what a kicker does, so a deep wire silently starves K and DST out
    # of the candidate pool entirely -- exactly when one of them is the upgrade
    # you needed.
    everyone = season_service.build_weekly_players(
        session, league, engine, week, availability={"FREEAGENT", "WAIVERS"}
    )
    by_position: dict[str, list] = {}
    for player in everyone:
        by_position.setdefault(player.position, []).append(player)
    free_agents = [
        player
        for group in by_position.values()
        for player in sorted(group, key=lambda p: p.season_points, reverse=True)[
            :WIRE_DEPTH_PER_POSITION
        ]
    ]

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

    verdicts = explain_by_position(
        roster, free_agents, shape, {t.player.espn_player_id for t in targets}
    )

    return {
        "week": week,
        "roster_size": len(roster),
        "roster_is_full": len(roster) >= shape.roster_size,
        "free_agents_considered": len(free_agents),
        "free_agents_available": len(everyone),
        # Why the list below looks the way it does. A wire that returns one
        # position reads as broken without this.
        "by_position": [
            {
                "position": v.position,
                "considered": v.considered,
                "best_name": v.best_name,
                "best_points": v.best_points,
                "incumbent_name": v.incumbent_name,
                "incumbent_points": v.incumbent_points,
                "helps": v.helps,
                "note": v.note,
            }
            for v in verdicts
        ],
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


def _serialize_proposal(p) -> dict:
    return {
        "their_team_id": p.their_team_id,
        "their_label": p.their_label,
        "give": [_serialize_player(x) for x in p.give],
        "receive": [_serialize_player(x) for x in p.receive],
        "my_week_delta": p.my_week_delta,
        "my_season_delta": p.my_season_delta,
        "their_week_delta": p.their_week_delta,
        "their_season_delta": p.their_season_delta,
        "my_delta": p.my_delta,
        "their_delta": p.their_delta,
        "kind": p.kind,
        "headline": p.headline,
        "reasons": p.reasons,
        "notes": p.notes,
    }


@router.get("/trade-finder")
def trade_finder(
    horizon: str = Query("season", pattern="^(season|week)$"),
    include_longshots: bool = Query(True),
    week: int | None = Query(None, ge=1, le=18),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Proposed trades with other teams that improve your starting lineup.

    `horizon` sets both the ranking and the accept/reject bar: `season`
    (rest-of-season lineup points) or `week` (win-now). Nothing is sent to ESPN
    -- these are ranked proposals to execute yourself.
    """
    resolved_week = _resolve_week(week, settings)
    result = season_service.propose_trades(
        session, league, engine, resolved_week,
        horizon=horizon, include_longshots=include_longshots,
    )
    return {
        "week": resolved_week,
        "horizon": result["horizon"],
        "reason": result.get("reason"),
        "mutual": [_serialize_proposal(p) for p in result["mutual"]],
        "longshots": [_serialize_proposal(p) for p in result["longshots"]],
    }


class TradePreviewRequest(BaseModel):
    their_team_id: int = Field(..., description="ESPN team id to propose the trade to.")
    give_ids: list[int] = Field(default_factory=list, description="Player ids you send.")
    receive_ids: list[int] = Field(default_factory=list, description="Player ids you receive.")


def require_trade_sender(user=Depends(require_user)):
    """Only accounts the owner has granted the trade-send capability get here."""
    if not getattr(user, "can_send_trades", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sending trades to ESPN is not enabled for your account.",
        )
    return user


@router.post("/trade/preview")
def trade_preview(
    payload: TradePreviewRequest,
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
    user=Depends(require_trade_sender),
) -> dict:
    """Stage 1 of send-to-ESPN: build the trade proposal and show it. Sends nothing.

    Gated on the owner-granted capability. Returns the exact request that a live
    send would make (method, url, body) plus a plain-English summary, so the
    payload can be verified against a real league before any network write is
    switched on. `sent` is always False here.
    """
    mine = season_service.my_team(session, league)
    if mine is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your team is not identified yet, so a trade cannot be addressed.",
        )
    their_team = next(
        (t for t in league.teams if t.espn_team_id == payload.their_team_id), None
    )
    if their_team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such team.")
    if their_team.espn_team_id == mine.espn_team_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That is your own team.",
        )

    by_id = {p.espn_player_id: p for p in engine.players}

    def resolve(ids: list[int]) -> list[trade_write.TradePlayer]:
        out = []
        for pid in ids:
            p = by_id.get(pid)
            if p is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Player {pid} is not in this league's pool.",
                )
            out.append(
                trade_write.TradePlayer(
                    espn_player_id=pid, name=p.name, position=p.position
                )
            )
        return out

    give = resolve(payload.give_ids)
    receive = resolve(payload.receive_ids)
    if not give and not receive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A trade needs at least one player on one side.",
        )

    preview = trade_write.preview_trade(
        season=league.season,
        league_id=league.espn_league_id,
        my_team_id=mine.espn_team_id,
        my_team_name=mine.name,
        their_team_id=their_team.espn_team_id,
        their_team_name=their_team.name,
        swid=settings.espn_swid,
        give=give,
        receive=receive,
    )
    return {
        "send_enabled": trade_write.SEND_ENABLED,
        "sent": preview.sent,
        "summary": preview.summary,
        "note": preview.note,
        "request": {"method": preview.method, "url": preview.url, "body": preview.body},
    }


@router.get("/roster")
def roster(
    week: int | None = Query(None, ge=1, le=18),
    team_id: int | None = Query(
        None, description="ESPN team id. Omit for my own roster."
    ),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    engine: ValuationEngine = Depends(engine_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """A roster with this week's numbers -- the picker for the trade screen.

    Takes a team id because a trade needs both sides. Without it this returned
    only my own roster, so the other team's picker was filtered against players
    that could never be in it and always came back empty.
    """
    week = _resolve_week(week, settings)

    if team_id is None:
        roster_ids = season_service.my_roster_ids(session, league)
        team_name = next((t.name for t in league.teams if t.is_mine), "My team")
    else:
        team = next((t for t in league.teams if t.espn_team_id == team_id), None)
        if team is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No team {team_id} in this league.",
            )
        roster_ids = season_service.team_roster_ids(league, team_id)
        team_name = team.name

    players = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=roster_ids
    )
    return {
        "week": week,
        "team_id": team_id,
        "team_name": team_name,
        "players": [_serialize_player(p) for p in players],
        "teams": [
            {"espn_team_id": t.espn_team_id, "name": t.name, "is_mine": t.is_mine}
            for t in league.teams
        ],
    }
