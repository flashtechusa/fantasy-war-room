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

from dataclasses import dataclass, field

from ..engine.roster import build_optimal_lineup
from ..models import AutoModeRun, League, UserEspnConfig
from ..services import season as season_service

#: Per-tier write master switches. Setting your own lineup is reversible and
#: only touches your team, so it is the first write that is live: the user
#: applies the optimal lineup to ESPN on demand from the Auto tab (a real write,
#: behind the install switch, the per-user capability, and an explicit confirm).
#: The remaining tiers stay False until each write's ESPN payload is captured.
LINEUP_WRITE_ENABLED = True
#: Waiver add/drop and FAAB claims write too, submitted per-target from the
#: Waivers tab behind a preview + explicit confirm (partly irreversible, so it
#: is not a one-tap flow). Trades stay surfaced-for-approval only.
WAIVER_WRITE_ENABLED = True
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
    #: The injured-reserve picture (see serialize_ir). Present whenever we can see
    #: the roster, tiers or not -- a jammed IR slot blocks moves either way.
    ir: dict | None = None


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


def current_slots_by_id(team) -> dict[int, str]:
    """Where ESPN currently has each of a team's players, keyed by player id."""
    from ..espn.constants import normalise_slot_label

    out: dict[int, str] = {}
    for entry in (getattr(team, "roster", None) or []):
        pid = entry.get("espn_player_id")
        if pid:
            out[int(pid)] = normalise_slot_label(entry.get("slot")) or "BE"
    return out


# ---------------------------------------------------------------------------
# Injured reserve
#
# An IR slot holds a hurt player without costing an active roster spot, which is
# free bench space -- but only while he still carries an IR-eligible tag. When he
# heals, ESPN BLOCKS every other roster move until he is off IR, so an unnoticed
# healed player silently jams waivers and trades.
# ---------------------------------------------------------------------------

#: Tags ESPN accepts in an IR slot. Leagues can be stricter (some allow only a
#: true IR designation), and ESPN rejects an ineligible move -- which we surface
#: rather than guess at, the same way we handle every other write it refuses.
IR_ELIGIBLE_STATUSES = {"OUT", "INJURY_RESERVE"}

#: What to do when a healed player must leave IR but the bench is full. Dropping
#: someone is irreversible, so "alert" (tell the user, change nothing) is the
#: default; "drop" lets Auto Mode clear the lowest-value bench player itself.
IR_RETURN_ALERT = "alert"
IR_RETURN_DROP = "drop"
IR_RETURN_CHOICES = (IR_RETURN_ALERT, IR_RETURN_DROP)


def ir_eligible(injury_status: str | None) -> bool:
    """Whether this tag may occupy an IR slot."""
    return (injury_status or "").upper() in IR_ELIGIBLE_STATUSES


def resolve_ir_return(config) -> str:
    """This user's choice for a healed player when the bench is full."""
    value = (getattr(config, "auto_ir_return", None) or IR_RETURN_ALERT).lower()
    return value if value in IR_RETURN_CHOICES else IR_RETURN_ALERT


@dataclass
class IrPlan:
    """What injured reserve should look like, versus what it looks like now."""

    #: Hurt players worth stashing: (player_id, name).
    to_ir: list[tuple[int, str]] = field(default_factory=list)
    #: Healed players who must leave IR and have bench room waiting.
    from_ir: list[tuple[int, str]] = field(default_factory=list)
    #: Healed players stuck on IR because the bench is full -- ESPN blocks all
    #: other roster moves until one of these is resolved.
    blocked: list[tuple[int, str]] = field(default_factory=list)
    #: Players who should stay exactly where they are, in IR.
    stay_in_ir: set[int] = field(default_factory=set)
    #: When the user asked for a drop, the cheapest bench player to give up:
    #: (player_id, name). Named only -- nothing here drops anyone.
    drop_candidate: tuple[int, str] | None = None
    #: The preference this plan was built under (see IR_RETURN_CHOICES).
    ir_return: str = IR_RETURN_ALERT
    ir_slots: int = 0
    ir_free: int = 0

    @property
    def needs_attention(self) -> bool:
        return bool(self.blocked)


def plan_ir(
    *,
    current_slots: dict[int, str],
    injury_by_id: dict[int, str],
    names: dict[int, str],
    ir_slots: int,
    bench_free: int,
) -> IrPlan:
    """Decide who belongs on IR, who must come off, and who is stuck there.

    `bench_free` is how many active roster spots are open right now, which is what
    decides whether a healed player can simply move back to the bench or whether
    somebody has to be dropped first.
    """
    plan = IrPlan(ir_slots=ir_slots)
    if ir_slots <= 0:
        return plan

    on_ir = [pid for pid, slot in current_slots.items() if (slot or "").upper() == "IR"]

    # Anyone already on IR either stays (still hurt) or has to come off (healed).
    for pid in on_ir:
        if ir_eligible(injury_by_id.get(pid)):
            plan.stay_in_ir.add(pid)
        elif bench_free > 0:
            plan.from_ir.append((pid, names.get(pid, str(pid))))
            bench_free -= 1
        else:
            plan.blocked.append((pid, names.get(pid, str(pid))))

    # Whatever IR room is left can hold hurt players currently taking up a spot.
    plan.ir_free = max(ir_slots - len(plan.stay_in_ir) - len(plan.blocked), 0)
    if plan.ir_free:
        candidates = [
            pid for pid, slot in current_slots.items()
            if (slot or "").upper() != "IR" and ir_eligible(injury_by_id.get(pid))
        ]
        for pid in candidates[: plan.ir_free]:
            plan.to_ir.append((pid, names.get(pid, str(pid))))
    # Report what is left once this plan is carried out, not before.
    plan.ir_free = max(plan.ir_free - len(plan.to_ir), 0)
    return plan


def cheapest_bench(roster, current_slots: dict[int, str], exclude: set[int]):
    """The bench player with the least to offer this week: (player_id, name).

    This is who a drop would cost. It is only ever *named* -- a drop is
    irreversible, so it goes through the same explicit confirm as every other
    irreversible write here.
    """
    bench = [
        p for p in roster
        if (current_slots.get(p.espn_player_id) or "BE").upper() == "BE"
        and p.espn_player_id not in exclude
    ]
    if not bench:
        return None
    worst = min(bench, key=lambda p: (p.projected_points, p.name))
    return (worst.espn_player_id, worst.name)


def ir_plan_for(
    roster,
    current_slots: dict[int, str],
    league,
    *,
    ir_return: str = IR_RETURN_ALERT,
) -> IrPlan:
    """The IR plan for my team right now, from the league's own IR slot count."""
    from ..engine.roster_move import active_roster_limit

    limit = active_roster_limit(
        getattr(league, "roster_slots", None), getattr(league, "bench_slots", 0)
    )
    # Players on IR do not consume an active spot -- that is the whole benefit.
    active_count = sum(
        1 for slot in current_slots.values() if (slot or "").upper() != "IR"
    )
    plan = plan_ir(
        current_slots=current_slots,
        injury_by_id={p.espn_player_id: p.injury_status for p in roster},
        names={p.espn_player_id: p.name for p in roster},
        ir_slots=int(getattr(league, "ir_slots", 0) or 0),
        bench_free=max(limit - active_count, 0),
    )
    plan.ir_return = ir_return if ir_return in IR_RETURN_CHOICES else IR_RETURN_ALERT
    if plan.blocked and plan.ir_return == IR_RETURN_DROP:
        # Name the player a drop would cost, so the alert is actionable. Auto Mode
        # still does not drop him: see IR_RETURN_DROP.
        plan.drop_candidate = cheapest_bench(
            roster, current_slots,
            exclude=plan.stay_in_ir | {pid for pid, _ in plan.to_ir},
        )
    return plan


def serialize_ir(plan: IrPlan) -> dict:
    """The IR picture for the API: who to stash, who must come off, who is stuck."""
    def rows(pairs):
        return [{"espn_player_id": pid, "name": name} for pid, name in pairs]

    return {
        "ir_slots": plan.ir_slots,
        "ir_free": plan.ir_free,
        "ir_return": plan.ir_return,
        "to_ir": rows(plan.to_ir),
        "from_ir": rows(plan.from_ir),
        "blocked": rows(plan.blocked),
        "drop_candidate": (
            {"espn_player_id": plan.drop_candidate[0], "name": plan.drop_candidate[1]}
            if plan.drop_candidate else None
        ),
        "needs_attention": plan.needs_attention,
    }


def ir_blocked_message(plan: IrPlan) -> str:
    """Plain-language version of a jammed IR slot, for the activity log."""
    stuck = ", ".join(name for _, name in plan.blocked)
    msg = (
        f"Healed on IR with a full bench: {stuck}. ESPN blocks other roster moves "
        f"until one is dropped or a spot opens."
    )
    if plan.drop_candidate:
        msg += f" Cheapest drop: {plan.drop_candidate[1]} (confirm it yourself)."
    return msg


def weekly_roster(session, league, engine, week: int, my_ids: set[int]):
    """My roster scored for THIS week, not for the season.

    This is the difference between a lineup that reads the week and one that does
    not. `engine.roster_players` carries the SEASON projection, so an elite player
    who is OUT or on bye this week still ranks first and the "optimal" lineup
    keeps starting him -- which is exactly why Auto Mode kept reporting "already
    optimal" while the Week screen correctly benched an OUT starter. Scoring
    through build_weekly_players applies the injury and bye rules for the week,
    and as_roster_player(use_week=True) carries those weekly points through.
    """
    from . import season as season_service

    weekly = season_service.build_weekly_players(
        session, league, engine, week, espn_player_ids=my_ids
    )
    return [p.as_roster_player(use_week=True) for p in weekly]


def lineup_moves(roster, shape, current_slots, ir_targets: dict[int, str] | None = None):
    """The slot changes to turn a team's current lineup into its optimal one.

    `roster` must already be scored for the week being set (see `weekly_roster`),
    so injuries and byes are reflected. `ir_targets` pins players to the IR slot
    (see `plan_ir`); without it every non-starter defaulted to the bench, which
    would have yanked a stashed player straight back off IR on the next cycle.
    Shared by the on-demand apply endpoint and the autonomous cycle so both diff
    identically. Only players whose slot changes produce a move.
    """
    from ..espn import lineup_write

    ir_targets = ir_targets or {}
    # Someone bound for IR cannot fill a starting slot, so keep him out of the
    # optimisation rather than letting a healed-but-stuck player be picked for a
    # slot he is not free to occupy.
    active = [p for p in roster if p.espn_player_id not in ir_targets]
    optimal = build_optimal_lineup(active, shape)
    names = {p.espn_player_id: p.name for p in roster}
    optimal_slot_by_id = {
        s.player.espn_player_id: s.slot for s in optimal.starters if s.player
    }
    for p in optimal.bench:
        optimal_slot_by_id.setdefault(p.espn_player_id, "BE")
    optimal_slot_by_id.update(ir_targets)
    return lineup_write.build_moves(
        optimal_slot_by_id=optimal_slot_by_id,
        current_slot_by_id=current_slots,
        names=names,
    )


def lineup_and_ir_moves(roster, shape, current_slots: dict[int, str], plan: IrPlan):
    """The week's moves, split into two ESPN transactions: lineup, then IR stash.

    They are deliberately separate. Leagues differ on what tag ESPN will accept in
    an IR slot, and ESPN validates a ROSTER transaction as a whole -- so a refused
    IR move bundled in with the lineup would take the lineup down with it. Setting
    the lineup first (with anyone bound for IR parked on the bench) means the part
    that matters every week always lands, and the stash is a second, independent
    write that can fail harmlessly.
    """
    from ..espn import lineup_write

    names = {p.espn_player_id: p.name for p in roster}
    # Phase one: nobody bound for IR is available to start, and the ones already
    # there stay put -- including a healed player we are not allowed to move.
    targets = {pid: "IR" for pid in plan.stay_in_ir}
    targets.update({pid: "IR" for pid, _ in plan.blocked})
    targets.update({pid: "BE" for pid, _ in plan.to_ir})
    lineup = lineup_moves(roster, shape, current_slots, ir_targets=targets)

    # Phase two: from where phase one leaves them, onto IR.
    after = dict(current_slots)
    for move in lineup:
        after[move.espn_player_id] = move.to_slot
    stash = lineup_write.build_moves(
        optimal_slot_by_id={pid: "IR" for pid, _ in plan.to_ir},
        current_slot_by_id=after,
        names=names,
    )
    return lineup, stash


def _current_starter_ids(team) -> set[int]:
    """Player ids ESPN currently has in a *starting* slot (not bench/IR)."""
    out: set[int] = set()
    for entry in (getattr(team, "roster", None) or []):
        pid = entry.get("espn_player_id")
        slot = (entry.get("slot") or "").upper()
        if pid and slot not in _BENCH_SLOTS:
            out.add(int(pid))
    return out


def build_lineup_plan(roster, shape, current_starters: set[int]) -> dict:
    """The optimal legal lineup vs what's currently started -- the moves to make.

    `roster` is scored for the week (see `weekly_roster`), so the plan shown on the
    Auto tab matches the Week screen rather than ranking on season value.
    """
    if not roster:
        return {"changes": [], "gain": 0.0, "note": "No roster yet."}
    lineup = build_optimal_lineup(roster, shape)
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
    if mine is not None and my_ids:
        roster = weekly_roster(session, league, engine, week, my_ids)
        # The IR picture is worth showing even with the lineup tier off: a healed
        # player left on IR blocks every other roster move the user tries to make.
        plan.ir = serialize_ir(ir_plan_for(
            roster, current_slots_by_id(mine), league,
            ir_return=resolve_ir_return(config),
        ))
        if tiers.lineup:
            plan.lineup = build_lineup_plan(
                roster, engine.shape, _current_starter_ids(mine),
            )
            plan.lineup["write_enabled"] = LINEUP_WRITE_ENABLED
            # Lineup writing is live and user-triggered: the plan shows the moves
            # and the Apply button on the Auto tab performs the real ESPN write.
            plan.lineup["status"] = (
                "ready_to_apply" if LINEUP_WRITE_ENABLED else "held_pending_capture"
            )

    if tiers.waivers:
        plan.waivers = {
            "faab_max": int(getattr(config, "auto_faab_max", 0) or 0),
            "write_enabled": WAIVER_WRITE_ENABLED,
            "status": "ready_to_apply" if WAIVER_WRITE_ENABLED else "held_pending_capture",
            "note": (
                "Ranked pickups are on the Waivers tab -- each shows an Add/Claim "
                "button that submits the add/drop to ESPN behind a confirm."
            ),
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
