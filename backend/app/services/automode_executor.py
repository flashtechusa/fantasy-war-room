"""One guarded Auto Mode execution cycle for one user.

Opening the Auto page never writes.  This module is called only by the manual
Run-now action or the background scheduler.  It fails closed unless the global
kill switch, owner-granted account capability, user opt-in, verified ESPN team,
and live ESPN session credentials are all present.

Lineups and the wire may execute.  Trades remain recommendation-only.
"""

from __future__ import annotations

from datetime import timedelta

from ..engine.waivers import recommend_waivers
from ..engine.weekly import BENCH_WARNING_STATUSES, optimise_lineup
from ..espn import lineup_write, wire_write
from ..espn.constants import normalise_slot_label
from ..espn.redaction import redact
from ..models import AutoModeRun, UserEspnConfig, utcnow
from . import season as season_service
from .board import league_shape
from .provider import build_provider

WIRE_DEPTH_PER_POSITION = 40
_DUPLICATE_WINDOW = timedelta(hours=24)
_WIRE_COOLDOWN = timedelta(hours=6)
MIN_WIRE_WEEK_GAIN = 1.0
MIN_WIRE_SEASON_GAIN = 5.0


def record(session, user, tier: str, status: str, summary: str) -> dict:
    """Credential-free activity record."""
    clean = redact(summary)[:600]
    session.add(
        AutoModeRun(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", "") or "",
            tier=tier,
            status=status,
            summary=clean,
        )
    )
    session.commit()
    return {"tier": tier, "status": status, "summary": clean}


def gate_problem(user, config: UserEspnConfig | None, settings) -> str | None:
    if not bool(getattr(settings, "auto_mode_enabled", False)):
        return "Auto Mode is switched off for this installation."
    if not bool(getattr(user, "can_auto_mode", False)):
        return "Auto Mode is not enabled for this account."
    if config is None or not bool(getattr(config, "auto_mode", False)):
        return "Auto Mode is turned off by the user."
    if not bool(getattr(config, "verified", False)):
        return "Auto Mode requires ESPN to verify which team this account owns. Reconnect ESPN."
    if not (getattr(settings, "espn_swid", None) and getattr(settings, "espn_s2", None)):
        return "ESPN session credentials are missing or expired; reconnect ESPN."
    if getattr(settings, "my_team_id", None) is None:
        return "Your ESPN team could not be resolved; reconnect ESPN."
    return None


def current_scoring_period(settings) -> int:
    """ESPN writes reject 0 once the current football scoring period is 1+."""
    try:
        return max(1, int(build_provider(settings).current_week or 1))
    except Exception:  # noqa: BLE001 - ESPN will still validate the write
        return 1


def _recent_exact(session, user, tier: str, identity: str) -> bool:
    return (
        session.query(AutoModeRun)
        .filter(
            AutoModeRun.user_id == getattr(user, "id", None),
            AutoModeRun.tier == tier,
            AutoModeRun.summary == identity[:600],
            AutoModeRun.status.in_(["executed", "pending"]),
            AutoModeRun.created_at >= utcnow() - _DUPLICATE_WINDOW,
        )
        .first()
        is not None
    )


def _wire_in_cooldown(session, user) -> bool:
    return (
        session.query(AutoModeRun)
        .filter(
            AutoModeRun.user_id == getattr(user, "id", None),
            AutoModeRun.tier == "waivers",
            AutoModeRun.status.in_(["executed", "pending"]),
            AutoModeRun.created_at >= utcnow() - _WIRE_COOLDOWN,
        )
        .first()
        is not None
    )


def _current_slots(team) -> dict[int, str]:
    out: dict[int, str] = {}
    for entry in team.roster or []:
        pid = entry.get("espn_player_id")
        if not pid:
            continue
        out[int(pid)] = normalise_slot_label(entry.get("slot")) or "BE"
    return out


def execute_lineup(session, league, engine, user, config, settings, week: int) -> dict:
    if not bool(getattr(config, "auto_lineup", False)):
        return {"tier": "lineup", "status": "disabled", "summary": "Auto-lineup is off."}

    mine = season_service.my_team(session, league)
    if mine is None:
        return record(session, user, "lineup", "error", "Your ESPN team could not be identified.")

    current = _current_slots(mine)
    active_ids = {pid for pid, slot in current.items() if slot != "IR"}
    if not active_ids:
        return record(session, user, "lineup", "skipped", "No active ESPN roster was found.")

    weekly = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=active_ids
    )
    by_id = {player.espn_player_id: player for player in weekly}
    missing = active_ids - set(by_id)
    if missing:
        return record(
            session,
            user,
            "lineup",
            "skipped",
            f"{len(missing)} active roster player(s) are missing current data; lineup cycle held.",
        )

    result = optimise_lineup(weekly, league_shape(league), week)
    if result.unfilled_slots:
        return record(
            session,
            user,
            "lineup",
            "skipped",
            "A complete legal starting lineup could not be formed; lineup cycle held.",
        )

    unsafe = [
        decision.player
        for decision in result.starters
        if decision.player is not None
        and (
            (decision.player.injury_status or "").upper() in BENCH_WARNING_STATUSES
            or decision.player.on_bye
        )
    ]
    if unsafe:
        names = ", ".join(player.name for player in unsafe[:3])
        return record(
            session,
            user,
            "lineup",
            "skipped",
            f"No fully safe lineup is available ({names}); lineup cycle held.",
        )

    target = {pid: "BE" for pid in active_ids}
    names = {pid: player.name for pid, player in by_id.items()}
    for decision in result.starters:
        if decision.player is not None:
            target[decision.player.espn_player_id] = decision.slot

    # The production lineup writer intentionally defaults unknown labels to
    # bench for manual use. Autonomous mode is stricter: unknown means STOP.
    known = set(lineup_write.LABEL_TO_SLOT_ID)
    unknown = {
        label
        for label in list(target.values()) + [current[pid] for pid in active_ids]
        if label not in known
    }
    if unknown:
        return record(
            session,
            user,
            "lineup",
            "skipped",
            f"Unknown ESPN lineup slot(s) {sorted(unknown)}; lineup cycle held.",
        )

    moves = lineup_write.build_moves(
        optimal_slot_by_id=target,
        current_slot_by_id=current,
        names=names,
    )
    if not moves:
        return record(
            session, user, "lineup", "skipped", "Lineup already optimal for this scoring period."
        )

    identity = "AUTO_LINEUP " + ",".join(
        f"{move.espn_player_id}:{move.from_slot}>{move.to_slot}"
        for move in sorted(moves, key=lambda m: m.espn_player_id)
    )
    if _recent_exact(session, user, "lineup", identity):
        return record(session, user, "lineup", "skipped", "Duplicate lineup write suppressed.")

    outcome = lineup_write.set_lineup(
        season=league.season,
        league_id=league.espn_league_id,
        team_id=mine.espn_team_id,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
        scoring_period_id=week,
        moves=moves,
    )
    if not outcome.ok:
        return record(
            session,
            user,
            "lineup",
            "error",
            f"ESPN rejected Auto-lineup (HTTP {outcome.status_code}): {outcome.response}",
        )

    record(session, user, "lineup", "executed", identity)
    return {
        "tier": "lineup",
        "status": "executed",
        "summary": f"Applied {len(moves)} lineup move(s) for scoring period {week}.",
    }


def _wire_targets(session, league, engine, settings, week: int):
    mine = season_service.my_team(session, league)
    roster_ids = season_service.my_roster_ids(session, league)
    roster = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=roster_ids
    )

    everyone = season_service.build_weekly_players(
        session, league, engine, week, availability={"FREEAGENT", "WAIVERS"}
    )
    rostered_ids: set[int] = set()
    for ids in season_service.rosters_by_team(session, league).values():
        rostered_ids.update(ids)
    everyone = [p for p in everyone if p.espn_player_id not in rostered_ids]

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

    current_slots = _current_slots(mine) if mine is not None else {}
    active_count = sum(1 for slot in current_slots.values() if slot != "IR")
    shape = league_shape(league)
    targets = recommend_waivers(
        roster=roster,
        free_agents=free_agents,
        shape=shape,
        week=week,
        faab_budget=league.acquisition_budget,
        faab_remaining=settings.faab_remaining,
        roster_is_full=active_count >= shape.roster_size,
        limit=15,
    )
    return roster, targets, current_slots, active_count


def execute_wire(session, league, engine, user, config, settings, week: int) -> dict:
    if not bool(getattr(config, "auto_waivers", False)):
        return {"tier": "waivers", "status": "disabled", "summary": "Auto-waivers are off."}
    if _wire_in_cooldown(session, user):
        return record(
            session,
            user,
            "waivers",
            "skipped",
            "Wire cooldown active after a recent successful add or claim.",
        )

    mine = season_service.my_team(session, league)
    if mine is None:
        return record(session, user, "waivers", "error", "Your ESPN team could not be identified.")

    roster, targets, current_slots, active_count = _wire_targets(
        session, league, engine, settings, week
    )
    if not targets:
        return record(session, user, "waivers", "skipped", "No wire move improves your lineup.")

    target = targets[0]
    if target.week_gain < MIN_WIRE_WEEK_GAIN and target.season_gain < MIN_WIRE_SEASON_GAIN:
        return record(
            session,
            user,
            "waivers",
            "skipped",
            f"Best wire upgrade ({target.player.name}) is below the automatic-move threshold.",
        )

    drop_id = target.drop.espn_player_id if target.drop is not None else None
    shape = league_shape(league)
    if active_count >= shape.roster_size and drop_id is None:
        return record(
            session,
            user,
            "waivers",
            "skipped",
            f"{target.player.name} helps, but the active roster is full and no safe drop was found.",
        )
    if drop_id is not None and current_slots.get(drop_id) == "IR" and active_count >= shape.roster_size:
        return record(
            session,
            user,
            "waivers",
            "skipped",
            "The suggested drop is on IR and would not safely free an active slot; wire cycle held.",
        )

    availability = (target.player.availability or "").upper()
    if availability not in {"FREEAGENT", "WAIVERS"}:
        return record(
            session,
            user,
            "waivers",
            "skipped",
            f"{target.player.name} is no longer a free agent or on waivers.",
        )

    cap = max(0, int(getattr(config, "auto_faab_max", 0) or 0))
    suggested = max(0, int(target.faab_bid or 0))
    bid = min(suggested, cap)
    if settings.faab_remaining is not None:
        bid = min(bid, max(0, int(settings.faab_remaining)))

    identity = (
        f"AUTO_{availability} add={target.player.espn_player_id} "
        f"drop={drop_id or 0} bid={bid if availability == 'WAIVERS' else 0}"
    )
    if _recent_exact(session, user, "waivers", identity):
        return record(session, user, "waivers", "skipped", "Duplicate wire transaction suppressed.")

    if availability == "FREEAGENT":
        body = wire_write.build_freeagent_body(
            team_id=mine.espn_team_id,
            swid=settings.espn_swid,
            scoring_period_id=week,
            add_player_id=target.player.espn_player_id,
            drop_player_id=drop_id,
        )
    else:
        body = wire_write.build_waiver_body(
            team_id=mine.espn_team_id,
            swid=settings.espn_swid,
            scoring_period_id=week,
            add_player_id=target.player.espn_player_id,
            drop_player_id=drop_id,
            bid_amount=bid if league.uses_faab else None,
        )

    outcome = wire_write.send_wire_transaction(
        season=league.season,
        league_id=league.espn_league_id,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
        body=body,
    )
    if not outcome.ok:
        return record(
            session,
            user,
            "waivers",
            "error",
            f"ESPN rejected Auto Mode wire move (HTTP {outcome.status_code}): {outcome.response}",
        )

    status = "pending" if availability == "WAIVERS" else "executed"
    record(session, user, "waivers", status, identity)
    summary = f"Add {target.player.name}"
    if target.drop is not None:
        summary += f"; drop {target.drop.name}"
    if availability == "WAIVERS" and league.uses_faab:
        summary += f"; bid {bid} FAAB"
    return {"tier": "waivers", "status": status, "summary": summary}


def trade_suggestion(session, league, engine, user, config, week: int) -> dict:
    if not bool(getattr(config, "auto_trades", False)):
        return {"tier": "trades", "status": "disabled", "summary": "Auto trade-finding is off."}

    try:
        found = season_service.propose_trades(
            session, league, engine, week, horizon="season", include_longshots=False
        )
        proposal = found.get("mutual", [None])[0] if found.get("mutual") else None
    except Exception as exc:  # noqa: BLE001 - recommendation failure must not block other tiers
        return record(session, user, "trades", "error", f"Trade finder failed: {exc}")

    if proposal is None:
        return record(session, user, "trades", "skipped", "No qualifying mutual trade found.")

    headline = getattr(proposal, "headline", None) or "Trade opportunity found"
    audit_summary = f"Approval required: {headline}"
    recent = (
        session.query(AutoModeRun)
        .filter(
            AutoModeRun.user_id == getattr(user, "id", None),
            AutoModeRun.tier == "trades",
            AutoModeRun.summary == audit_summary[:600],
            AutoModeRun.created_at >= utcnow() - _DUPLICATE_WINDOW,
        )
        .first()
    )
    if recent is None:
        record(session, user, "trades", "needs_approval", audit_summary)
    return {"tier": "trades", "status": "needs_approval", "summary": headline}


def run_cycle(session, league, engine, user, config, settings) -> dict:
    """Run one autonomous decision/execution cycle for one account."""
    problem = gate_problem(user, config, settings)
    if problem:
        return {"active": False, "reason": problem, "actions": []}

    week = current_scoring_period(settings)
    actions: list[dict] = []
    if bool(getattr(config, "auto_lineup", False)):
        actions.append(execute_lineup(session, league, engine, user, config, settings, week))
    if bool(getattr(config, "auto_waivers", False)):
        actions.append(execute_wire(session, league, engine, user, config, settings, week))
    if bool(getattr(config, "auto_trades", False)):
        actions.append(trade_suggestion(session, league, engine, user, config, week))

    if not actions:
        actions.append(record(session, user, "cycle", "skipped", "No Auto Mode tiers are enabled."))
    return {"active": True, "week": week, "actions": actions}
