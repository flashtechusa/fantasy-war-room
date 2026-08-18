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
        "fantasypros_key_set": bool(settings.fantasypros_api_key),
        "has_private_credentials": settings.has_espn_credentials,
        "ready_for_espn": settings.can_reach_espn,
        "sources": {key: source(key) for key in OVERRIDABLE},
    }


# ---------------------------------------------------------------------------
# Per-user configuration
# ---------------------------------------------------------------------------


def user_config(session: Session, user) -> "UserEspnConfig | None":
    from ..models import UserEspnConfig

    if user is None:
        return None
    return session.scalars(
        select(UserEspnConfig).where(UserEspnConfig.user_id == user.id)
    ).first()


def settings_for_user(session: Session, user, base: Settings | None = None) -> Settings:
    """Settings as they apply to one signed-in person.

    Layered: environment, then the install-wide overrides, then this user's own
    ESPN connection. The user layer is what makes the app multi-tenant -- two
    people signed in at once resolve to two different leagues.

    A user with no connection of their own falls through to the install-wide
    one. That keeps a single-user install (the owner's) working exactly as it
    did before accounts existed.
    """
    from ..services import secrets as secret_store

    base = effective_settings(session, base)
    config = user_config(session, user)
    if config is None:
        return base

    merged = base.model_dump()
    if config.espn_league_id is not None:
        merged["espn_league_id"] = config.espn_league_id
    if config.espn_season is not None:
        merged["espn_season"] = config.espn_season
    if config.espn_swid_encrypted:
        merged["espn_swid"] = secret_store.decrypt(config.espn_swid_encrypted)
    if config.espn_s2_encrypted:
        merged["espn_s2"] = secret_store.decrypt(config.espn_s2_encrypted)
    if config.my_team_id is not None:
        merged["my_team_id"] = config.my_team_id
    if config.faab_remaining is not None:
        merged["faab_remaining"] = config.faab_remaining

    # A user with their own league is not in demo mode, whatever the install
    # default says -- otherwise their real league would be silently ignored.
    if config.espn_league_id is not None:
        merged["demo_mode"] = False

    return Settings.model_validate(merged)


def save_user_config(session: Session, user, values: dict) -> "UserEspnConfig":
    """Store one user's ESPN connection, encrypting the cookies."""
    from ..models import UserEspnConfig
    from ..services import secrets as secret_store

    config = user_config(session, user)
    if config is None:
        config = UserEspnConfig(user_id=user.id)
        session.add(config)

    if "espn_league_id" in values and values["espn_league_id"] is not None:
        config.espn_league_id = int(values["espn_league_id"])
    if "espn_season" in values and values["espn_season"] is not None:
        config.espn_season = int(values["espn_season"])
    if "my_team_id" in values:
        config.my_team_id = values["my_team_id"]
    if "faab_remaining" in values:
        config.faab_remaining = values["faab_remaining"]

    # Blank means "leave what is stored"; explicit empty string clears it.
    if values.get("espn_swid"):
        config.espn_swid_encrypted = secret_store.encrypt(values["espn_swid"])
    elif values.get("espn_swid") == "":
        config.espn_swid_encrypted = ""
    if values.get("espn_s2"):
        config.espn_s2_encrypted = secret_store.encrypt(values["espn_s2"])
    elif values.get("espn_s2") == "":
        config.espn_s2_encrypted = ""

    session.flush()
    return config
