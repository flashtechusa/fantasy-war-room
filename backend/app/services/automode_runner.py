"""Background and manual runner for Auto Mode.

Every run re-reads the kill switch and user settings, refreshes ESPN roster
state before deciding, and isolates each account in its own database session.
A failure for one account cannot stop the scheduler for everybody else.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..db import session_scope
from ..espn.redaction import redact
from ..models import User
from . import automode_executor
from .board import build_engine, clear_cache
from .importer import import_free_agents, import_league
from .provider import build_provider
from .runtime_config import effective_settings, settings_for_user, user_config

log = logging.getLogger(__name__)

POLL_SECONDS = 30 * 60
INITIAL_DELAY_SECONDS = 60


def run_user_once(user_id: int) -> dict:
    """Run the same guarded cycle used by both the API and scheduler."""
    with session_scope() as session:
        user = session.get(User, int(user_id))
        if user is None or not user.enabled:
            return {"active": False, "reason": "User is disabled or missing.", "actions": []}

        config = user_config(session, user)
        settings = settings_for_user(session, user, get_settings())
        problem = automode_executor.gate_problem(user, config, settings)
        if problem:
            return {"active": False, "reason": problem, "actions": []}
        if settings.espn_league_id is None:
            return {"active": False, "reason": "No ESPN league is connected.", "actions": []}

        try:
            provider = build_provider(settings)
            # Frequent operational refresh: rules + every roster, not full draft
            # history/player history. This is the ownership truth Auto Mode acts on.
            league = import_league(
                session,
                provider=provider,
                settings=settings,
                include_players=False,
                include_history=False,
            )
            week = automode_executor.current_scoring_period(settings)
            if config is not None and bool(getattr(config, "auto_waivers", False)):
                import_free_agents(
                    session,
                    league,
                    provider=provider,
                    settings=settings,
                    week=week,
                    limit=300,
                )
            clear_cache()
            engine = build_engine(
                session,
                league,
                active_source=settings.projection_mode or "espn",
            )
            return automode_executor.run_cycle(
                session, league, engine, user, config, settings
            )
        except Exception as exc:  # noqa: BLE001 - fail one account closed
            safe = redact(str(exc))
            automode_executor.record(
                session, user, "cycle", "error", f"Auto Mode cycle failed: {safe}"
            )
            log.warning("Auto Mode cycle for user %s failed: %s", user.id, safe)
            return {"active": False, "reason": safe, "actions": []}


def run_all_users_once() -> list[dict]:
    """Run all currently eligible users once."""
    with session_scope() as session:
        if not bool(effective_settings(session).auto_mode_enabled):
            return []
        ids: list[int] = []
        for user in session.query(User).filter(
            User.enabled.is_(True), User.can_auto_mode.is_(True)
        ).all():
            config = user_config(session, user)
            if config is not None and bool(getattr(config, "auto_mode", False)):
                ids.append(user.id)

    results: list[dict] = []
    for user_id in ids:
        results.append({"user_id": user_id, **run_user_once(user_id)})
    return results


async def scheduler_loop() -> None:
    """Half-hourly low-volume loop; cancellation on application shutdown is normal."""
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            await asyncio.to_thread(run_all_users_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep future passes alive
            log.warning("Auto Mode scheduler pass failed: %s", redact(str(exc)))
        await asyncio.sleep(POLL_SECONDS)
