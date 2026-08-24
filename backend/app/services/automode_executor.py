"""Live Auto Mode execution behind the three Auto Mode gates.

One cycle may set the current lineup and make at most one wire move.  Trades are
intentionally recommendation-only: Auto Mode can find them, but another manager
should never receive a machine-generated offer without the user's approval.

The executor is fail-closed.  It needs the install switch, owner-granted account
capability, the user's own opt-in, a resolved team, and live ESPN cookies.  It
never retries a mutation, never logs credentials, and records every outcome in
``AutoModeRun``.
"""

from __future__ import annotations

from datetime import timedelta

from ..engine.waivers import recommend_waivers
from ..engine.weekly import BENCH_WARNING_STATUSES, optimise_lineup
from ..espn.constants import SLOT_ID_TO_LABEL
from ..espn.redaction import redact
from ..espn.transaction_write import (
    build_freeagent_body,
    build_lineup_body,
    build_waiver_body,
    send_transaction,
)
from ..models import AutoModeRun, UserEspnConfig, utcnow
from . import season as season_service
from .board import league_shape
from .provider import build_provider

_SLOT_TO_ID = {label: slot_id for slot_id, label in SLOT_ID_TO_LABEL.items()}
_SLOT_TO_ID["BN"] = 20  # defensive alias; ESPN normally normalises this to BE
WIRE_DEPTH_PER_POSITION = 40
_DUPLICATE_WINDOW = timedelta(hours=24)


def _record(session, user, tier: str, status: str, summary: str) -> dict:
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


def _recently_done(session, user, tier: str, summary: str) -> bool:
    return (
        session.query(AutoModeRun)
        .filter(
            AutoModeRun.user_id == getattr(user, "id", None),
            AutoModeRun.tier == tier,
            AutoModeRun.summary == summary[:600],
            AutoModeRun.status.in_(["executed", "pending"]),
            AutoModeRun.created_at >= utcnow() - _DUPLICATE_WINDOW,
        )
        .first()
        is not None
    )


def _current_week(settings) -> int:
    try:
        # ESPN uses scoring period 1 during the football preseason even when
        # some read surfaces still report 0.
        return max(1, int(build_provider(settings).current_week or 1))
    except Exception:  # noqa: BLE001 - ESPN validates the eventual write
        return 1


def _gates(user, config: UserEspnConfig | None, settings) -> str | None:
    if not bool(getattr(settings, "auto_mode_enabled", False)):
        return "Auto Mode is switched off for this installation."
    if not bool(getattr(user, "can_auto_mode", False)):
        return "Auto Mode is not enabled for this account."
    if config is None or not bool(getattr(config, "auto_mode", False)):
        return "Auto Mode is turned off by the user."
    if not (getattr(settings, "espn_swid", None) and getattr(settings, "espn_s2", None)):
        return "ESPN session credentials are missing or expired; reconnect ESPN."
    return None


def _lineup_moves(session, league, engine, week: int) -> tuple[list[tuple[int, int, int]], str]:
    mine = season_service.my_team(session, league)
    if mine is None:
        return [], "Your ESPN team could not be identified."

    current: dict[int, str] = {}
    for row in mine.roster or []:
        pid = row.get("espn_player_id")
        if pid:
            current[int(pid)] = (row.get("slot") or "").upper()

    weekly = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=set(current)
    )
    by_weekly_id = {player.espn_player_id: player for player in weekly}

    # If even one active-roster player is missing from our player/projection
    # store, do nothing. Treating "unknown" as "bench" is unacceptable when
    # the decision will be executed unattended.
    expected = {pid for pid, slot in current.items() if slot not in {"IR", "ER"}}
    missing = expected - set(by_weekly_id)
    if missing:
        return [], f"{len(missing)} roster player(s) are missing current data; lineup cycle held."

    # Leave IR/ER alone. Activating one can require a separate roster move or
    # drop. Everyone else participates in the weekly optimiser; ESPN OUT/bye
    # players already carry zero weekly points, so a healthy replacement wins
    # naturally. If the best legal lineup still contains an unavailable player,
    # hold the cycle instead of making a partial/empty lineup.
    candidates = [
        player
        for player in weekly
        if current.get(player.espn_player_id, "") not in {"IR", "ER"}
    ]
    optimal = optimise_lineup(candidates, league_shape(league), week)
    unsafe = [
        decision.player
        for decision in optimal.starters
        if decision.player is not None
        and (
            (decision.player.injury_status or "").upper() in BENCH_WARNING_STATUSES
            or decision.player.on_bye
        )
    ]
    if unsafe:
        names = ", ".join(player.name for player in unsafe[:3])
        return [], f"No fully safe lineup is available ({names}); lineup cycle held."

    target: dict[int, str] = {
        decision.player.espn_player_id: decision.slot
        for decision in optimal.starters
        if decision.player is not None
    }

    changes: list[tuple[int, int, int]] = []
    for pid, from_label in current.items():
        if from_label in {"IR", "ER"}:
            continue
        to_label = target.get(pid, "BE")
        if from_label == to_label or (from_label == "BN" and to_label == "BE"):
            continue
        from_id = _SLOT_TO_ID.get(from_label)
        to_id = _SLOT_TO_ID.get(to_label)
        if from_id is None or to_id is None:
            return [], f"Unknown ESPN lineup slot {from_label or to_label}; cycle held."
        changes.append((pid, from_id, to_id))

    if not changes:
        return [], "Lineup already optimal under the safe Auto Mode rules."
    return changes, f"Set {len(changes)} lineup slot change(s) for week {week}."


def execute_lineup(session, league, engine, user, config, settings, week: int) -> dict:
    if not bool(getattr(config, "auto_lineup", False)):
        return {"tier": "lineup", "status": "disabled", "summary": "Auto-lineup is off."}

    mine = season_service.my_team(session, league)
    if mine is None:
        return _record(session, user, "lineup", "error", "Your ESPN team could not be identified.")

    moves, summary = _lineup_moves(session, league, engine, week)
    if not moves:
        return _record(session, user, "lineup", "skipped", summary)

    identity = "LINEUP " + ",".join(f"{p}:{a}>{b}" for p, a, b in sorted(moves))
    if _recently_done(session, user, "lineup", identity):
        return _record(session, user, "lineup", "skipped", "Duplicate lineup transaction suppressed.")

    body = build_lineup_body(
        team_id=mine.espn_team_id,
        swid=settings.espn_swid,
        scoring_period_id=week,
        moves=moves,
    )
    result = send_transaction(
        season=league.season,
        league_id=league.espn_league_id,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
        body=body,
    )
    if result.ok:
        # Store the action identity as the successful row so rapid scheduler
        # reruns cannot repeat it before the next roster refresh lands.
        _record(session, user, "lineup", "executed", identity)
        return {"tier": "lineup", "status": "executed", "summary": summary}
    return _record(
        session,
        user,
        "lineup",
        "error",
        f"ESPN rejected Auto-lineup (HTTP {result.status_code}): {result.response}",
    )


def _rank_wire(session, league, engine, settings, week: int):
    roster_ids = season_service.my_roster_ids(session, league)
    roster = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=roster_ids
    )
    everyone = season_service.build_weekly_players(
        session, league, engine, week, availability={"FREEAGENT", "WAIVERS"}
    )

    # Player.availability is season-global in the cache while ownership is
    # league-specific. Fresh ESPN rosters are therefore the final authority:
    # never offer an "available" cached player who is on any team in this league.
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
        for player in sorted(group, key=lambda p: p.season_points, reverse=True)[:WIRE_DEPTH_PER_POSITION]
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
        limit=15,
    )
    return roster, targets


def execute_wire(session, league, engine, user, config, settings, week: int) -> dict:
    if not bool(getattr(config, "auto_waivers", False)):
        return {"tier": "waivers", "status": "disabled", "summary": "Auto-waivers are off."}

    mine = season_service.my_team(session, league)
    if mine is None:
        return _record(session, user, "waivers", "error", "Your ESPN team could not be identified.")

    roster, targets = _rank_wire(session, league, engine, settings, week)
    if not targets:
        return _record(session, user, "waivers", "skipped", "No wire move improves your lineup.")

    target = targets[0]
    drop_id = target.drop.espn_player_id if target.drop is not None else None
    shape = league_shape(league)
    if len(roster) >= shape.roster_size and drop_id is None:
        return _record(
            session, user, "waivers", "skipped",
            f"{target.player.name} helps, but the roster is full and no safe drop was found.",
        )

    availability = (target.player.availability or "").upper()
    if availability not in {"FREEAGENT", "WAIVERS"}:
        return _record(
            session, user, "waivers", "skipped",
            f"{target.player.name} is no longer a free agent or on waivers.",
        )

    cap = max(0, int(getattr(config, "auto_faab_max", 0) or 0))
    remaining = getattr(settings, "faab_remaining", None)
    suggested = max(0, int(target.faab_bid or 0))
    bid = min(suggested, cap)
    if remaining is not None:
        bid = min(bid, max(0, int(remaining)))

    action = (
        f"{availability} add {target.player.espn_player_id} "
        f"drop {drop_id or 0} bid {bid if availability == 'WAIVERS' else 0}"
    )
    if _recently_done(session, user, "waivers", action):
        return _record(session, user, "waivers", "skipped", "Duplicate wire move suppressed.")

    if availability == "FREEAGENT":
        body = build_freeagent_body(
            team_id=mine.espn_team_id,
            swid=settings.espn_swid,
            scoring_period_id=week,
            add_player_id=target.player.espn_player_id,
            drop_player_id=drop_id,
        )
    else:
        body = build_waiver_body(
            team_id=mine.espn_team_id,
            swid=settings.espn_swid,
            scoring_period_id=week,
            add_player_id=target.player.espn_player_id,
            drop_player_id=drop_id,
            bid_amount=bid if league.uses_faab else None,
        )

    result = send_transaction(
        season=league.season,
        league_id=league.espn_league_id,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
        body=body,
    )
    if not result.ok:
        return _record(
            session,
            user,
            "waivers",
            "error",
            f"ESPN rejected wire move (HTTP {result.status_code}): {result.response}",
        )

    status = "pending" if availability == "WAIVERS" else "executed"
    _record(session, user, "waivers", status, action)
    human = f"Add {target.player.name}"
    if target.drop is not None:
        human += f"; drop {target.drop.name}"
    if availability == "WAIVERS" and league.uses_faab:
        human += f"; bid {bid} FAAB"
    return {"tier": "waivers", "status": status, "summary": human}


def trade_suggestion(session, league, engine, user, config, week: int) -> dict:
    if not bool(getattr(config, "auto_trades", False)):
        return {"tier": "trades", "status": "disabled", "summary": "Auto trade-finding is off."}
    try:
        result = season_service.propose_trades(
            session, league, engine, week, horizon="season", include_longshots=False
        )
        proposal = result.get("mutual", [None])[0] if result.get("mutual") else None
    except Exception as exc:  # noqa: BLE001
        return _record(session, user, "trades", "error", f"Trade finder failed: {exc}")
    if proposal is None:
        return _record(session, user, "trades", "skipped", "No qualifying mutual trade found.")
    headline = getattr(proposal, "headline", None) or "Trade opportunity found"
    summary = f"Approval required: {headline}"
    # Do not spam the same recommendation every scheduler pass.
    recent = (
        session.query(AutoModeRun)
        .filter(
            AutoModeRun.user_id == getattr(user, "id", None),
            AutoModeRun.tier == "trades",
            AutoModeRun.summary == summary[:600],
            AutoModeRun.created_at >= utcnow() - _DUPLICATE_WINDOW,
        )
        .first()
    )
    if recent is None:
        _record(session, user, "trades", "needs_approval", summary)
    return {"tier": "trades", "status": "needs_approval", "summary": headline}


def run_cycle(session, league, engine, user, config, settings) -> dict:
    """Run one autonomous cycle for one account."""
    problem = _gates(user, config, settings)
    if problem:
        return {"active": False, "reason": problem, "actions": []}

    week = _current_week(settings)
    actions: list[dict] = []
    if bool(getattr(config, "auto_lineup", False)):
        actions.append(execute_lineup(session, league, engine, user, config, settings, week))
    if bool(getattr(config, "auto_waivers", False)):
        actions.append(execute_wire(session, league, engine, user, config, settings, week))
    if bool(getattr(config, "auto_trades", False)):
        actions.append(trade_suggestion(session, league, engine, user, config, week))

    if not actions:
        actions.append(_record(session, user, "cycle", "skipped", "No Auto Mode tiers are enabled."))
    return {"active": True, "week": week, "actions": actions}
