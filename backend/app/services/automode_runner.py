"""The autonomous Auto Mode cycle -- runs a team on a schedule, no browser open.

This is what finally makes Auto Mode *auto*. A scheduled process (the Windows
task `scripts/windows/auto-mode.ps1`, or the owner's "Run now" button) calls
`run_cycle`, which for every fully-enabled user sets their optimal lineup on
ESPN. It is the exact write the Auto tab performs by hand, minus the tap: the
standing consent is the owner's per-account grant plus the user's own opt-in,
both off by default, plus the install-wide switch.

Staging, same discipline as every other write here:

    LINEUP  -- executes. It only touches your own team and is fully reversible,
               and its ESPN write is verified, so the cycle performs it.
    WAIVERS -- planned and logged only (AUTO_WAIVER_EXECUTE is False). A drop is
               not reversible and a FAAB bid is real money, so the cycle does not
               fire claims until that write is confirmed against a live response
               and autonomous spending is explicitly turned on.
    TRADES  -- never auto-fired; surfaced for one-tap approval elsewhere.

Every action -- performed, held, or skipped -- is written to the Auto Mode
activity log with no credentials, so a scheduled run is as auditable as a manual
one. One user's failure never aborts the cycle for the others.
"""

from __future__ import annotations

import logging

from ..config import get_settings
from ..models import AutoModeRun, User
from . import automode, season as season_service
from .board import build_engine
from .importer import get_active_league, refresh_rosters
from .runtime_config import effective_settings, settings_for_user, user_config

log = logging.getLogger(__name__)

#: Lineup writing is verified and reversible, so the cycle performs it. Waivers
#: stay planned-only until their write is confirmed live and autonomous FAAB
#: spending is explicitly enabled.
AUTO_LINEUP_EXECUTE = True
AUTO_WAIVER_EXECUTE = False


def _log(session, user, tier: str, statusname: str, summary: str) -> None:
    from ..espn.redaction import redact

    session.add(AutoModeRun(
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", "") or "",
        tier=tier, status=statusname, summary=redact(summary)[:600],
    ))
    session.commit()


def eligible_users(session, *, install_on: bool) -> list[User]:
    """Users the cycle should act for: install on, granted, enabled, opted in."""
    if not install_on:
        return []
    granted = session.query(User).filter(
        User.can_auto_mode.is_(True), User.enabled.is_(True)
    ).all()
    out: list[User] = []
    for user in granted:
        config = user_config(session, user)
        if config is not None and getattr(config, "auto_mode", False):
            out.append(user)
    return out


def _week(settings) -> int:
    from .provider import build_provider

    try:
        return max(int(build_provider(settings).current_week), 1)
    except Exception:      # noqa: BLE001 - a bad week is not worth aborting a cycle
        return 1


def _apply_lineup(session, league, settings, user, mine) -> dict:
    """Set this user's optimal lineup on ESPN. The same write the Auto tab makes."""
    from ..espn import lineup_write

    engine = build_engine(
        session, league, active_source=settings.projection_mode or "espn",
        allow_fantasypros=bool(settings.fantasypros_api_key),
    )
    my_ids = season_service.my_roster_ids(session, league)
    if not my_ids:
        _log(session, user, "lineup", "skipped", "No roster found for your team.")
        return {"tier": "lineup", "status": "skipped", "detail": "no roster"}

    moves = automode.lineup_moves(engine, my_ids, automode.current_slots_by_id(mine))
    result = lineup_write.set_lineup(
        season=league.season,
        league_id=league.espn_league_id,
        team_id=mine.espn_team_id,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
        scoring_period_id=_week(settings),
        moves=moves,
    )
    summary = (
        "Lineup already optimal -- no change." if not moves
        else "; ".join(f"{m.name} {m.from_slot}->{m.to_slot}" for m in moves)
    )
    statusname = "applied" if result.ok else "rejected"
    _log(session, user, "lineup", statusname, f"HTTP {result.status_code}: {summary}")
    log.info(
        "Auto cycle set lineup for %s (team %s): ok=%s status=%s moves=%s",
        getattr(user, "username", "?"), mine.espn_team_id, result.ok,
        result.status_code, len(moves),
    )
    return {
        "tier": "lineup", "status": statusname, "ok": result.ok,
        "status_code": result.status_code, "moves": len(moves),
    }


def _run_user(session, user, base_settings) -> dict:
    """One user's cycle: refresh, set the lineup, hold waivers. Never raises."""
    out: dict = {"user": getattr(user, "username", "?"), "actions": []}
    try:
        settings = settings_for_user(session, user, get_settings())
        if not (settings.espn_swid and settings.espn_s2):
            _log(session, user, "lineup", "skipped", "No ESPN connection for this account.")
            out["actions"].append({"tier": "lineup", "status": "skipped", "detail": "no cookies"})
            return out

        # Refresh with this user's cookies -- also re-points is_mine at their team,
        # so the cycle sets the right lineup even with several users in one league.
        refresh_rosters(session, settings)

        league = get_active_league(session, settings)
        if league is None:
            _log(session, user, "lineup", "skipped", "No league imported for this account.")
            out["actions"].append({"tier": "lineup", "status": "skipped", "detail": "no league"})
            return out
        mine = season_service.my_team(session, league)
        if mine is None:
            _log(session, user, "lineup", "skipped", "Your team is not identified yet.")
            out["actions"].append({"tier": "lineup", "status": "skipped", "detail": "no team"})
            return out

        tiers = automode.resolve_tiers(user_config(session, user))
        if tiers.lineup and AUTO_LINEUP_EXECUTE:
            out["actions"].append(_apply_lineup(session, league, settings, user, mine))
        if tiers.waivers:
            # Held: autonomous claims spend FAAB and drop players, so they wait for
            # the waiver write to be confirmed live and for AUTO_WAIVER_EXECUTE.
            _log(session, user, "waivers", "held",
                 "Autonomous waiver claims are staged off; run them from the Waivers tab.")
            out["actions"].append({"tier": "waivers", "status": "held"})
    except Exception as exc:      # noqa: BLE001 - one user must not abort the cycle
        session.rollback()
        log.exception("Auto cycle failed for %s", getattr(user, "username", "?"))
        _log(session, user, "lineup", "error", f"Cycle error: {exc}")
        out["actions"].append({"tier": "lineup", "status": "error", "detail": str(exc)})
    return out


def run_cycle(session, *, only_user_id: int | None = None) -> dict:
    """Run the autonomous cycle for every eligible user (or just one).

    Returns a per-user summary. Writes nothing when the install switch is off.
    """
    base = effective_settings(session, get_settings())
    install_on = bool(getattr(base, "auto_mode_enabled", False))
    if not install_on:
        return {"install_enabled": False, "ran": [],
                "note": "Auto Mode is switched off for this installation."}

    users = eligible_users(session, install_on=install_on)
    if only_user_id is not None:
        users = [u for u in users if u.id == only_user_id]

    ran = [_run_user(session, user, base) for user in users]
    log.info("Auto cycle complete: %s user(s) processed.", len(ran))
    return {"install_enabled": True, "ran": ran}
