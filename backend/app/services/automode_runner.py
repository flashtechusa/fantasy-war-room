"""Background scheduler for Auto Mode.

The loop is intentionally boring: wake periodically, re-read the install kill
switch, then run each eligible account in its own database session.  Before a
write cycle it refreshes ESPN's league/rosters and (when enabled) the wire so
an unattended process does not act on yesterday's ownership state.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..db import session_scope
from ..espn.redaction import redact
from ..models import AutoModeRun, User
from . import automode_executor
from .board import build_engine, clear_cache
from .importer import get_active_league, import_free_agents, import_league
from .provider import build_provider
from .runtime_config import effective_settings, settings_for_user, user_config

log = logging.getLogger(__name__)

#: Half-hourly is frequent enough to catch normal NFL roster changes without
#: hammering an undocumented endpoint.  A manual Run now endpoint is available
#: for immediate execution.
POLL_SECONDS = 30 * 60
INITIAL_DELAY_SECONDS = 60


def _log_error(session, user, message: str) -> None:
    session.add(
        AutoModeRun(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", "") or "",
            tier="cycle",
            status="error",
            summary=redact(message)[:600],
        )
    )
    session.commit()


def run_user_once(user_id: int) -> dict:
    """Refresh and run one account. Safe to call from the API or scheduler."""
    with session_scope() as session:
        user = session.get(User, int(user_id))
        if user is None or not user.enabled:
            return {"active": False, "reason": "User is disabled or missing.", "actions": []}

        config = user_config(session, user)
        settings = settings_for_user(session, user, get_settings())
        gate_problem = automode_executor._gates(user, config, settings)  # noqa: SLF001
        if gate_problem:
            return {"active": False, "reason": gate_problem, "actions": []}

        if settings.espn_league_id is None:
            _log_error(session, user, "No ESPN league is connected to this account.")
            return {"active": False, "reason": "No ESPN league connected.", "actions": []}

        try:
            provider = build_provider(settings)
            # Refresh rosters/settings first. No player-history import here: this
            # is a frequent operational poll, not a full league import.
            league = import_league(
                session,
                provider=provider,
                settings=settings,
                include_players=False,
                include_history=False,
            )
            if config is not None and bool(getattr(config, "auto_waivers", False)):
                import_free_agents(
                    session,
                    league,
                    provider=provider,
                    settings=settings,
                    week=automode_executor._current_week(settings),  # noqa: SLF001
                )
            clear_cache()
            engine = build_engine(
                session, league, active_source=settings.projection_mode or "espn"
            )
            return automode_executor.run_cycle(
                session, league, engine, user, config, settings
            )
        except Exception as exc:  # noqa: BLE001 - isolate one user's failure
            safe = redact(str(exc))
            _log_error(session, user, f"Auto Mode cycle failed: {safe}")
            log.warning("Auto Mode cycle for user %s failed: %s", user.id, safe)
            return {"active": False, "reason": safe, "actions": []}


def run_all_users_once() -> list[dict]:
    """Run every account whose capability + opt-in are currently enabled."""
    with session_scope() as session:
        if not bool(effective_settings(session).auto_mode_enabled):
            return []
        ids = [
            u.id
            for u in session.query(User).filter(
                User.enabled.is_(True), User.can_auto_mode.is_(True)
            ).all()
            if bool(getattr(user_config(session, u), "auto_mode", False))
        ]

    results = []
    for user_id in ids:
        results.append({"user_id": user_id, **run_user_once(user_id)})
    return results


async def scheduler_loop() -> None:
    """Process-wide loop. Cancellation on app shutdown is expected."""
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(run_all_users_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep scheduler alive
            log.warning("Auto Mode scheduler pass failed: %s", redact(str(exc)))
        await asyncio.sleep(POLL_SECONDS)
