"""Runtime ESPN configuration, settable from the UI.

Lets you point the app at a league without editing a file -- necessary when
you're running it somewhere you only have a browser.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..espn.client import EspnClient, EspnConnectionError
from ..services import board as board_service
from ..services import runtime_config
from .routes_auth import require_user
from .routes_admin import owner_only as require_owner
from ..services.runtime_config import (
    clear_overrides,
    describe,
    effective_settings,
    write_overrides,
)

router = APIRouter(prefix="/api/config", tags=["config"])


class EspnConfigRequest(BaseModel):
    espn_league_id: int | None = Field(default=None, ge=1)
    espn_season: int | None = Field(default=None, ge=2000, le=2100)
    espn_swid: str | None = None
    espn_s2: str | None = None
    demo_mode: bool | None = None
    my_team_id: int | None = None
    my_draft_slot: int | None = Field(default=None, ge=1, le=32)
    faab_remaining: int | None = Field(default=None, ge=0, le=100000)
    fantasypros_api_key: str | None = None

    # Reject unknown fields rather than dropping them. A missing field here
    # meant a saved API key was silently discarded while the app reported
    # success -- failing loudly would have caught it immediately.
    model_config = {"extra": "forbid"}


@router.get("/mine")
def read_my_config(
    session: Session = Depends(get_db), user=Depends(require_user)
) -> dict:
    """This user's own ESPN connection. Secrets are reported as set/unset."""
    config = runtime_config.user_config(session, user)
    if config is None:
        return {
            "configured": False,
            "espn_league_id": None,
            "espn_season": None,
            "swid_set": False,
            "espn_s2_set": False,
            "my_team_id": None,
            "faab_remaining": None,
        }
    return {
        "configured": config.espn_league_id is not None,
        "espn_league_id": config.espn_league_id,
        "espn_season": config.espn_season,
        "swid_set": bool(config.espn_swid_encrypted),
        "espn_s2_set": bool(config.espn_s2_encrypted),
        "my_team_id": config.my_team_id,
        "faab_remaining": config.faab_remaining,
    }


@router.put("/mine")
def save_my_config(
    payload: EspnConfigRequest,
    session: Session = Depends(get_db),
    user=Depends(require_user),
) -> dict:
    """Point this account at its own league.

    Separate from the install-wide settings so two people can use one install
    without seeing each other's team. Cookies are encrypted before storage.
    """
    values = payload.model_dump(exclude_unset=True)
    # Install-wide keys are not settable from a personal connection. Letting a
    # normal user through here would let them switch off demo mode for
    # everyone, or overwrite the owner's provider key.
    for install_wide in ("demo_mode", "fantasypros_api_key"):
        values.pop(install_wide, None)

    runtime_config.save_user_config(session, user, values)
    session.commit()
    board_service.clear_cache()

    result: dict = {
        "saved": True,
        "config": read_my_config(session, user),
        "connection": None,
    }

    # Test with *this user's* resolved settings, so the answer is about their
    # league rather than whatever the install happens to point at.
    settings = runtime_config.settings_for_user(session, user)
    if settings.can_reach_espn:
        client = EspnClient(
            league_id=settings.espn_league_id,
            season=settings.espn_season,
            swid=settings.espn_swid,
            espn_s2=settings.espn_s2,
        )
        try:
            result["connection"] = {"connected": True, **client.check_connection()}
        except EspnConnectionError as exc:
            result["connection"] = {"connected": False, "detail": str(exc)}
    return result


@router.get("")
def read_config(
    session: Session = Depends(get_db), user=Depends(require_owner)
) -> dict:
    """Install-wide configuration. Owner only.

    This reports the fallback league every account without its own connection
    inherits, so it is the owner's league id -- not something to hand to every
    signed-in user. Personal settings live at `/api/config/mine`.

    Cookies are reported as set/unset, never returned.
    """
    return describe(session)


@router.put("")
def update_config(
    payload: EspnConfigRequest,
    session: Session = Depends(get_db),
    user=Depends(require_owner),
) -> dict:
    """Save install-wide ESPN configuration and test the connection.

    Owner only. A normal user configures their own connection through
    `/api/config/mine`; this one is the fallback every account without a
    connection of its own inherits, so it is not theirs to change.

    Only the fields you send are changed; send an empty string to clear one.
    """
    write_overrides(session, payload.model_dump(exclude_unset=True))
    board_service.clear_cache()

    settings = effective_settings(session)
    result: dict = {"saved": True, "config": describe(session), "connection": None}

    if settings.can_reach_espn:
        client = EspnClient(
            league_id=settings.espn_league_id,
            season=settings.espn_season,
            swid=settings.espn_swid,
            espn_s2=settings.espn_s2,
        )
        try:
            result["connection"] = {"connected": True, **client.check_connection()}
        except EspnConnectionError as exc:
            result["connection"] = {"connected": False, "detail": str(exc)}
    return result


@router.delete("")
def reset_config(
    session: Session = Depends(get_db), user=Depends(require_owner)
) -> dict:
    """Drop UI-entered configuration and fall back to the environment."""
    clear_overrides(session)
    board_service.clear_cache()
    return {"cleared": True, "config": describe(session)}
