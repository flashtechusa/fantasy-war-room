"""Runtime configuration overlay.

Credentials can come from the environment (`.env`, a Codespaces secret, a
Docker env file) *or* be entered in the UI. UI values win, because if you
bothered to type them into the running app that's clearly the intent.

This is what makes the app usable somewhere you only have a browser -- a
Codespace, a tablet, a phone -- with no file editing at all.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import AppConfig

#: Keys that may be set at runtime, mapped to the Settings field they override.
OVERRIDABLE = {
    "espn_league_id": int,
    "espn_season": int,
    "espn_swid": str,
    "espn_s2": str,
    "demo_mode": bool,
    "my_team_id": int,
    "my_draft_slot": int,
    "faab_remaining": int,
    "fantasypros_api_key": str,
}

#: Never returned by the API.
SECRET_KEYS = {"espn_swid", "espn_s2", "fantasypros_api_key"}


def _coerce(key: str, raw: str):
    kind = OVERRIDABLE[key]
    if raw == "":
        return None
    if kind is int:
        return int(raw)
    if kind is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return raw


def read_overrides(session: Session) -> dict:
    """Stored runtime overrides, coerced to their Settings types."""
    out: dict = {}
    for row in session.scalars(select(AppConfig)).all():
        if row.key not in OVERRIDABLE:
            continue
        try:
            value = _coerce(row.key, row.value)
        except (TypeError, ValueError):
            continue
        if value is not None:
            out[row.key] = value
    return out


def effective_settings(session: Session, base: Settings | None = None) -> Settings:
    """Environment settings with runtime overrides applied on top."""
    base = base or get_settings()
    overrides = read_overrides(session)
    if not overrides:
        return base
    # Re-validate so the SWID brace-normalisation and blank handling still run.
    merged = base.model_dump()
    merged.update(overrides)
    return Settings.model_validate(merged)


def write_overrides(session: Session, values: dict) -> None:
    """Persist runtime overrides. A value of None clears that key."""
    existing = {row.key: row for row in session.scalars(select(AppConfig)).all()}
    for key, value in values.items():
        if key not in OVERRIDABLE:
            continue
        if value is None or value == "":
            if key in existing:
                session.delete(existing[key])
            continue
        raw = "true" if value is True else "false" if value is False else str(value)
        row = existing.get(key)
        if row is None:
            session.add(AppConfig(key=key, value=raw))
        else:
            row.value = raw
    session.commit()


def clear_overrides(session: Session) -> None:
    for row in session.scalars(select(AppConfig)).all():
        session.delete(row)
    session.commit()


def describe(session: Session, base: Settings | None = None) -> dict:
    """Safe-to-display configuration state. Secrets are reported, never returned."""
    base = base or get_settings()
    settings = effective_settings(session, base)
    overrides = read_overrides(session)

    def source(key: str) -> str:
        return "ui" if key in overrides else "environment"

    return {
        "espn_league_id": settings.espn_league_id,
        "espn_season": settings.espn_season,
        "demo_mode": settings.demo_mode,
        "my_team_id": settings.my_team_id,
        "my_draft_slot": settings.my_draft_slot,
        "faab_remaining": overrides.get("faab_remaining"),
        "swid_set": bool(settings.espn_swid),
        "espn_s2_set": bool(settings.espn_s2),
        "has_private_credentials": settings.has_espn_credentials,
        "ready_for_espn": settings.can_reach_espn,
        "sources": {key: source(key) for key in OVERRIDABLE},
    }
