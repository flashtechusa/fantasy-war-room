"""Live Auto Mode controls.

The existing /api/season/automode endpoint is the readable planner/settings
surface.  This router adds the operational side: a manual run using the exact
same function the background scheduler invokes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AutoModeRun
from ..services import automode_runner, runtime_config
from .routes_auth import require_user

router = APIRouter(prefix="/api/season/automode", tags=["season"])


@router.post("/run")
def run_now(
    session: Session = Depends(get_db),
    user=Depends(require_user),
) -> dict:
    """Run this account's Auto Mode cycle now.

    This is a real-action endpoint when all three gates and a tier are enabled.
    It does not bypass any scheduler/executor guard; it calls the same code path.
    """
    config = runtime_config.user_config(session, user)
    if not bool(getattr(user, "can_auto_mode", False)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Auto Mode is not enabled for your account.",
        )
    if config is None or not bool(getattr(config, "auto_mode", False)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Turn Auto Mode on for your account first.",
        )
    result = automode_runner.run_user_once(user.id)
    return result


@router.get("/activity")
def activity(
    limit: int = 50,
    session: Session = Depends(get_db),
    user=Depends(require_user),
) -> dict:
    limit = max(1, min(int(limit), 200))
    rows = (
        session.query(AutoModeRun)
        .filter(AutoModeRun.user_id == user.id)
        .order_by(AutoModeRun.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "activity": [
            {
                "at": row.created_at,
                "tier": row.tier,
                "status": row.status,
                "summary": row.summary,
            }
            for row in rows
        ]
    }
