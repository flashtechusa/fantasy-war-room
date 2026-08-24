"""Operational Auto Mode endpoint.

The status/settings surface remains in routes_season.  This endpoint performs a
cycle through the exact same runner used by the background scheduler; it does
not bypass any gate or safety check.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import automode_runner, runtime_config
from .routes_auth import require_user

router = APIRouter(prefix="/api/season/automode", tags=["season"])


@router.post("/run")
def run_now(
    session: Session = Depends(get_db),
    user=Depends(require_user),
) -> dict:
    if not bool(getattr(user, "can_auto_mode", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Auto Mode is not enabled for your account.",
        )
    config = runtime_config.user_config(session, user)
    if config is None or not bool(getattr(config, "auto_mode", False)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Turn Auto Mode on for your account first.",
        )
    if not bool(getattr(config, "verified", False)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Reconnect ESPN so the app can verify which team you own before autonomous writes.",
        )
    return automode_runner.run_user_once(user.id)
