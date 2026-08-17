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


@router.get("")
def read_config(session: Session = Depends(get_db)) -> dict:
    """Current effective configuration. Cookies are reported as set/unset only."""
    return describe(session)


@router.put("")
def update_config(
    payload: EspnConfigRequest,
    session: Session = Depends(get_db),
) -> dict:
    """Save ESPN configuration and immediately test the connection.

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
def reset_config(session: Session = Depends(get_db)) -> dict:
    """Drop UI-entered configuration and fall back to the environment."""
    clear_overrides(session)
    board_service.clear_cache()
    return {"cleared": True, "config": describe(session)}
