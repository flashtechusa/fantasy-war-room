"""Auto Mode -- autonomous team management, staged and dry-run first.

Auto Mode is the natural extension of everything the app already computes: the
optimal lineup, the waiver ranker, the trade finder. What's new is *acting* on
them on a schedule. Acting on ESPN is a write, and -- exactly like the trade
sender -- the writes are staged:

    NOW (this module): plan and log. Auto Mode decides what it *would* do and
    records it, but performs no ESPN writes. The two write flags below are False
    until each write's payload is captured from ESPN's own UI (lineup set,
    add/drop, waiver claim), the same way the trade write was captured.

    LATER: flip a write flag once its payload is verified, and that tier goes
    live behind the same guardrails (install switch, per-user capability, the
    user's own opt-in, an audit trail).

So today Auto Mode is a safe, honest planner: it shows and logs the moves it
would make. Nothing leaves the app.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..engine.roster import build_optimal_lineup
from ..models import AutoModeRun, League, UserEspnConfig
from ..services import season as season_service

#: Per-tier write master switches. Each stays False until that write's ESPN
#: payload is captured and verified; while False the tier plans but never writes.
LINEUP_WRITE_ENABLED = False
WAIVER_WRITE_ENABLED = False
#: Auto Mode never fires trades at leaguemates on its own -- trades are always
#: surfaced for the user's one-tap approval, never auto-executed.
TRADE_AUTO_EXECUTE = False

#: Roster slots that are not starting spots.
_BENCH_SLOTS = {"BE", "BN", "IR"}


@dataclass
class Tiers:
    lineup: bool = False
    waivers: bool = False
    trades: bool = False


@dataclass
class AutoPlan:
    """What Auto Mode would do this cycle. Dry-run: nothing here was executed."""

    active: bool = False
    dry_run: bool = True
    reason: str | None = None
    lineup: dict | None = None
    waivers: dict | None = None
    trades: dict | None = None


def resolve_tiers(config: UserEspnConfig | None) -> Tiers:
    if config is None:
        return Tiers()
    return Tiers(
        lineup=bool(getattr(config, "auto_lineup", False)),
        waivers=bool(getattr(config, "auto_waivers", False)),
        trades=bool(getattr(config, "auto_trades", False)),
    )


def is_active(*, install_on: bool, capable: bool, user_on: bool) -> bool:
    """Auto Mode runs only when all three line up -- install, capability, opt-in."""
    return bool(install_on and capable and user_on)


def _current_starter_ids(team) -> set[int]:
    """Player ids ESPN currently has in a *starting* slot (not bench/IR)."""
    out: set[int] = set()
    for entry in (getattr(team, "roster", None) or []):
        pid = entry.get("espn_player_id")
        slot = (entry.get("slot") or "").upper()
        if pid and slot not in _BENCH_SLOTS:
            out.add(int(pid))
    return out


def build_lineup_plan(engine, my_ids: set[int], current_starters: set[int]) -> dict:
    """The optimal legal lineup vs what's currently started -- the moves to make."""
    roster = engine.roster_players(my_ids)
    if not roster:
        return {"changes": [], "gain": 0.0, "note": "No roster yet."}
    lineup = build_optimal_lineup(roster, engine.shape)
    optimal = {s.player.espn_player_id for s in lineup.starters if s.player}
    by_id = {p.espn_player_id: p for p in roster}

    to_start = [by_id[i] for i in (optimal - current_starters) if i in by_id]
    to_sit = [by_id[i] for i in (current_starters - optimal) if i in by_id]

    current_total = round(sum(by_id[i].projected_points for i in current_starters if i in by_id), 1)
    gain = round(lineup.total_points - current_total, 1)

    def show(p) -> dict:
        return {
            "espn_player_id": p.espn_player_id, "name": p.name,
            "position": p.position, "projected_points": round(p.projected_points, 1),
        }

    return {
        "optimal_points": lineup.total_points,
        "current_points": current_total,
        "gain": gain,
        "start": [show(p) for p in sorted(to_start, key=lambda x: -x.projected_points)],
        "sit": [show(p) for p in sorted(to_sit, key=lambda x: x.projected_points)],
        "already_optimal": not to_start and not to_sit,
    }


def build_plan(
    session,
    league: League,
    engine,
    user,
    config: UserEspnConfig | None,
    *,
    install_on: bool,
    week: int,
    trade_headline: str | None = None,
) -> AutoPlan:
    """Compute (never execute) what Auto Mode would do for this user right now."""
    capable = bool(getattr(user, "can_auto_mode", False))
    user_on = bool(getattr(config, "auto_mode", False)) if config else False
    active = is_active(install_on=install_on, capable=capable, user_on=user_on)
    tiers = resolve_tiers(config)

    plan = AutoPlan(active=active, dry_run=True)
    if not active:
        plan.reason = (
            "Auto Mode is off." if not user_on
            else "Not enabled for your account." if not capable
            else "Auto Mode is switched off for this installation."
        )
        return plan

    mine = season_service.my_team(session, league)
    my_ids = season_service.my_roster_ids(session, league)
    if tiers.lineup and mine is not None and my_ids:
        plan.lineup = build_lineup_plan(engine, my_ids, _current_starter_ids(mine))
        plan.lineup["write_enabled"] = LINEUP_WRITE_ENABLED
        plan.lineup["status"] = "would_execute" if LINEUP_WRITE_ENABLED else "held_pending_capture"

    if tiers.waivers:
        plan.waivers = {
            "faab_max": int(getattr(config, "auto_faab_max", 0) or 0),
            "write_enabled": WAIVER_WRITE_ENABLED,
            "status": "held_pending_capture",
            "note": "Ranked pickups are on the Waivers tab; autonomous claims need the ESPN capture.",
        }

    if tiers.trades:
        plan.trades = {
            "headline": trade_headline,
            "auto_execute": TRADE_AUTO_EXECUTE,
            "status": "needs_approval",
            "note": "Auto Mode surfaces a trade for your one-tap approval; it never fires trades on its own.",
        }
    return plan


def log_cycle(session, user, plan: AutoPlan) -> None:
    """Write the activity rows for a planning cycle. Credential-free."""
    rows = []
    if plan.lineup is not None:
        if plan.lineup.get("already_optimal"):
            summary = "Lineup already optimal -- no change."
        else:
            summary = (
                f"Would start {len(plan.lineup.get('start', []))}, "
                f"sit {len(plan.lineup.get('sit', []))} (+{plan.lineup.get('gain', 0)} pts)."
            )
        rows.append(("lineup", plan.lineup.get("status", "planned"), summary))
    if plan.waivers is not None:
        rows.append(("waivers", plan.waivers["status"], plan.waivers["note"]))
    if plan.trades is not None:
        head = plan.trades.get("headline") or "no qualifying trade"
        rows.append(("trades", plan.trades["status"], f"Trade suggestion: {head}"))
    for tier, status, summary in rows:
        session.add(AutoModeRun(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", "") or "",
            tier=tier, status=status, summary=summary[:600],
        ))
    if rows:
        session.commit()
