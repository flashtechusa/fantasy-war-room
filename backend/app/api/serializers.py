"""Dataclass/ORM -> JSON conversion.

Kept in one place so the shape the frontend sees is easy to audit, and so the
"show your working" fields (score components, reasons, replacement levels) are
never accidentally dropped from a response.
"""

from __future__ import annotations

from dataclasses import asdict

from ..engine.draft_math import DraftPosition
from ..engine.league_shape import LeagueShape
from ..engine.roster import (
    ByeConflict,
    OptimalLineup,
    PositionNeed,
    RosterStrength,
)
from ..engine.scoring import LeagueScoring
from ..engine.simulate import SimulationResult
from ..engine.valuation import BoardResult, PlayerValuation
from ..models import DraftPick, DraftSession, HistoricalDraftPick, League, LeagueTeam


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def serialize_team(team: LeagueTeam) -> dict:
    return {
        "espn_team_id": team.espn_team_id,
        "name": team.name,
        "abbrev": team.abbrev,
        "owners": team.owners or [],
        "logo_url": team.logo_url,
        "division_name": team.division_name,
        "draft_slot": team.draft_slot,
        "is_mine": team.is_mine,
        "record": {"wins": team.wins, "losses": team.losses, "ties": team.ties},
        "roster": team.roster or [],
        "roster_size": len(team.roster or []),
    }


def serialize_league(league: League, scoring: LeagueScoring, shape: LeagueShape) -> dict:
    raw = league.raw_settings or {}
    return {
        "id": league.id,
        "espn_league_id": league.espn_league_id,
        "season": league.season,
        "name": league.name,
        "source": league.source,
        "is_demo": league.source == "demo",
        "imported_at": league.imported_at,
        "team_count": league.team_count,
        "scoring": {
            "type": league.scoring_type,
            "format_label": scoring.format_label,
            "is_ppr": league.is_ppr,
            "ppr_value": league.ppr_value,
            "points_per_passing_td": scoring.points_per_passing_td,
            "rules": [
                {
                    "stat_id": rule.stat_id,
                    "abbrev": rule.abbrev,
                    "label": rule.label,
                    "points": rule.points,
                    "points_overrides": rule.points_overrides or {},
                }
                for rule in sorted(league.scoring_rules, key=lambda r: r.stat_id)
                if rule.points != 0 or rule.points_overrides
            ],
            "rule_count": len(league.scoring_rules),
        },
        "roster": {
            "starting_slots": league.roster_slots or {},
            "all_slot_counts": raw.get("all_slot_counts") or {},
            "bench_slots": league.bench_slots,
            "ir_slots": league.ir_slots,
            "starters_total": shape.starter_slots,
            "roster_size": shape.roster_size,
            "draft_rounds": shape.draft_rounds,
            "flex_slots": [
                {"label": f.label, "count": f.count, "eligible": list(f.eligible)}
                for f in shape.flex
            ],
            "is_superflex": shape.is_superflex(),
            "shape_summary": shape.describe(),
        },
        "draft": {
            "type": league.draft_type,
            "order": league.draft_order or [],
            "completed": league.draft_completed,
            "keeper_count": league.keeper_count,
            "date": raw.get("draft_date"),
            "seconds_per_pick": raw.get("draft_time_per_selection"),
        },
        "waivers": {
            "type": league.waiver_type,
            "uses_faab": league.uses_faab,
            "budget": league.acquisition_budget,
            "process_days": league.waiver_process_days or [],
            "waiver_hours": raw.get("waiver_hours"),
            "acquisition_limit": raw.get("acquisition_limit"),
        },
        "playoffs": {
            "regular_season_weeks": league.regular_season_weeks,
            "team_count": league.playoff_team_count,
            "matchup_length": league.playoff_matchup_length,
            "seed_tie_rule": league.playoff_seed_tie_rule,
        },
        "trades": {
            "deadline": league.trade_deadline,
            "veto_votes_required": league.veto_votes_required,
        },
        "teams": [serialize_team(team) for team in league.teams],
    }


def serialize_history(picks: list[HistoricalDraftPick]) -> dict:
    by_season: dict[int, list[dict]] = {}
    for pick in picks:
        by_season.setdefault(pick.season, []).append(
            {
                "overall_pick": pick.overall_pick,
                "round_num": pick.round_num,
                "round_pick": pick.round_pick,
                "team_name": pick.team_name,
                "espn_team_id": pick.espn_team_id,
                "player_name": pick.player_name,
                "espn_player_id": pick.espn_player_id,
                "keeper": pick.keeper,
                "bid_amount": pick.bid_amount,
            }
        )
    return {
        "seasons": sorted(by_season.keys(), reverse=True),
        "picks_by_season": {
            str(season): sorted(picks, key=lambda p: p["overall_pick"])
            for season, picks in by_season.items()
        },
    }


# ---------------------------------------------------------------------------
# Phases 3 & 4
# ---------------------------------------------------------------------------


def serialize_valuation(value: PlayerValuation, detail: bool = False) -> dict:
    payload = {
        "espn_player_id": value.espn_player_id,
        "name": value.name,
        "position": value.position,
        "pro_team": value.pro_team,
        "bye_week": value.bye_week,
        "rank": value.overall_rank,
        "position_rank": value.position_rank,
        "tier": value.tier,
        "projected_points": value.projected_points,
        "points_per_game": value.points_per_game,
        "espn_projected_points": value.espn_projected_points,
        "vor": value.vor,
        "replacement_points": value.replacement_points,
        "adp": value.adp,
        "adp_delta": value.adp_delta,
        "espn_rank": value.espn_rank,
        "scarcity": value.scarcity_score,
        "draft_score": value.draft_score,
        "availability_next_pick": value.availability_next_pick,
        "gone_probability": value.gone_probability,
        "opportunity_cost": value.opportunity_cost,
        "wait_cost": value.wait_cost,
        "upside_points": value.upside_points,
        "floor_points": value.floor_points,
        "injury_status": value.injury_status,
        "percent_owned": value.percent_owned,
        "projected_games": value.projected_games,
        "value_grade": value.value_grade,
        "flags": value.flags,
        "is_drafted": value.is_drafted,
        "drafted_by_me": value.drafted_by_me,
    }
    if detail:
        payload["components"] = [asdict(c) for c in value.components]
        payload["reasons"] = value.reasons
        payload["why_now"] = value.why_now
        payload["why_wait"] = value.why_wait
        payload["principal_risk"] = value.principal_risk
    return payload


def serialize_position(position: DraftPosition) -> dict:
    return {
        "current_pick": position.current_pick,
        "round": position.round_num,
        "pick_in_round": position.pick_in_round,
        "on_the_clock_slot": position.on_the_clock_slot,
        "my_slot": position.my_slot,
        "is_my_pick": position.is_my_pick,
        "my_next_pick": position.my_next_pick,
        "picks_until_my_next": position.picks_until_my_next,
        "my_upcoming_picks": position.my_upcoming_picks,
        "rounds": position.rounds,
        "rounds_remaining": position.rounds_remaining,
        "team_count": position.team_count,
        "draft_type": position.draft_type,
    }


def serialize_board_meta(board: BoardResult) -> dict:
    return {
        "scoring_format": board.scoring_format,
        "league_shape": board.league_shape,
        "source": board.generated_from,
        "market_drift": board.market_drift,
        "current_pick": board.current_pick,
        "next_pick": board.next_pick,
        "picks_until_next": board.picks_until_next,
        "replacement": board.replacement.as_dict(),
        "position_decay": board.position_decay,
        "scarcity": {
            position: {
                "position": s.position,
                "starters_left": s.starters_left,
                "tier_players_left": s.tier_players_left,
                "cliff_points": s.cliff_points,
                "picks_to_cliff": s.picks_to_cliff,
                "dropoff_to_next_pick": s.dropoff_to_next_pick,
                "scarcity_score": s.scarcity_score,
                "expected_best_next": s.expected_best_next,
                "best_available_now": s.best_available_now,
                "explanation": s.explain(),
            }
            for position, s in board.scarcity.items()
        },
        "needs": {
            position: {
                "position": need.position,
                "rostered": need.rostered,
                "starters_required": need.starters_required,
                "starters_filled": need.starters_filled,
                "marginal_value": need.marginal_value,
                "need_score": need.need_score,
                "urgent": need.urgent,
                "note": need.note,
            }
            for position, need in board.needs.items()
        },
    }


# ---------------------------------------------------------------------------
# Phase 5
# ---------------------------------------------------------------------------


def serialize_pick(pick: DraftPick) -> dict:
    return {
        "overall_pick": pick.overall_pick,
        "round": pick.round_num,
        "round_pick": pick.round_pick,
        "draft_slot": pick.draft_slot,
        "espn_player_id": pick.espn_player_id,
        "player_name": pick.player_name,
        "position": pick.position,
        "pro_team": pick.pro_team,
        "is_mine": pick.is_mine,
        "source": pick.source,
    }


def serialize_draft_session(draft: DraftSession) -> dict:
    return {
        "id": draft.id,
        "name": draft.name,
        "my_draft_slot": draft.my_draft_slot,
        "team_count": draft.team_count,
        "rounds": draft.rounds,
        "draft_type": draft.draft_type,
        "espn_sync_enabled": draft.espn_sync_enabled,
        "last_synced_at": draft.last_synced_at,
        "picks_made": len(draft.picks),
        "picks": [serialize_pick(p) for p in sorted(draft.picks, key=lambda p: p.overall_pick)],
    }


# ---------------------------------------------------------------------------
# Phase 6
# ---------------------------------------------------------------------------


def serialize_lineup(lineup: OptimalLineup) -> dict:
    return {
        "starters": [
            {
                "slot": assignment.slot,
                "player": (
                    {
                        "espn_player_id": assignment.player.espn_player_id,
                        "name": assignment.player.name,
                        "position": assignment.player.position,
                        "pro_team": assignment.player.pro_team,
                        "projected_points": round(assignment.player.projected_points, 1),
                        "vor": round(assignment.player.vor, 1),
                        "bye_week": assignment.player.bye_week,
                        "injury_status": assignment.player.injury_status,
                    }
                    if assignment.player
                    else None
                ),
            }
            for assignment in lineup.starters
        ],
        "bench": [
            {
                "espn_player_id": player.espn_player_id,
                "name": player.name,
                "position": player.position,
                "pro_team": player.pro_team,
                "projected_points": round(player.projected_points, 1),
                "vor": round(player.vor, 1),
                "bye_week": player.bye_week,
                "injury_status": player.injury_status,
            }
            for player in lineup.bench
        ],
        "total_points": lineup.total_points,
        "weekly_points": lineup.weekly_points,
        "empty_slots": lineup.empty_slots,
    }


def serialize_strengths(strengths: list[RosterStrength]) -> list[dict]:
    return [
        {
            "position": s.position,
            "starters_points": s.starters_points,
            "league_average_points": s.league_average_points,
            "edge": s.edge,
            "grade": s.grade,
        }
        for s in strengths
    ]


def serialize_conflicts(conflicts: list[ByeConflict]) -> list[dict]:
    return [
        {
            "week": c.week,
            "players": c.players,
            "positions": c.positions,
            "severity": c.severity,
        }
        for c in conflicts
    ]


def serialize_needs(needs: dict[str, PositionNeed]) -> list[dict]:
    return sorted(
        (
            {
                "position": need.position,
                "rostered": need.rostered,
                "starters_required": need.starters_required,
                "starters_filled": need.starters_filled,
                "marginal_value": need.marginal_value,
                "need_score": need.need_score,
                "urgent": need.urgent,
                "note": need.note,
            }
            for need in needs.values()
        ),
        key=lambda n: n["marginal_value"],
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Phase 7
# ---------------------------------------------------------------------------


def serialize_simulation(result: SimulationResult) -> dict:
    return {
        "simulations": result.simulations,
        "my_slot": result.my_slot,
        "team_count": result.team_count,
        "rounds": result.rounds,
        "seed": result.seed,
        "expected_roster_points": {
            "mean": result.mean_roster_points,
            "p10": result.p10_roster_points,
            "p90": result.p90_roster_points,
        },
        "mean_starter_points_by_position": result.mean_starter_points_by_position,
        "strategy": result.strategy,
        "picks": [
            {
                "overall_pick": pick.overall_pick,
                "round": pick.round_num,
                "position_share": dict(pick.position_counts),
                "expected_best_vor": pick.expected_best_vor,
                "mean_taken_vor": pick.mean_taken_vor,
                "likely_available": [
                    {"name": name, "position": position, "probability": probability}
                    for name, position, probability in pick.likely_available
                ],
            }
            for pick in result.picks
        ],
        "wait_profiles": [
            {
                "position": profile.position,
                "by_round": profile.by_round,
                "cliff_round": profile.cliff_round,
                "verdict": profile.verdict,
                "note": profile.note,
            }
            for profile in result.wait_profiles
        ],
    }
