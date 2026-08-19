"""Connect ESPN -- the guided connection flow.

Every endpoint here obeys the same rule: cookies go *in*, never *out*. No
response body on this router contains a SWID or an espn_s2, and every error
message has been through `redact()` before it reaches the client.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..espn.client import EspnConnectionError
from ..espn.redaction import redact
from ..models import League, Player, User
from ..services import board as board_service
from ..services import espn_connect
from ..services import runtime_config
from ..services.importer import import_league
from ..services.provider import build_provider
from .deps import settings_dep
from .routes_auth import require_user
from .serializers import serialize_league

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/espn", tags=["espn"])

#: Failed pairing-code redemptions per client address. A code is 8 characters
#: from a 32-symbol alphabet and single-use, so guessing is already hopeless;
#: this exists so that trying anyway is also slow and visible.
_MAX_REDEEM_FAILURES = 8
_REDEEM_WINDOW_SECONDS = 300
_redeem_failures: dict[str, list[float]] = {}


def _throttle_key(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _check_redeem_throttle(key: str) -> None:
    now = time.monotonic()
    recent = [t for t in _redeem_failures.get(key, []) if now - t < _REDEEM_WINDOW_SECONDS]
    _redeem_failures[key] = recent
    if len(recent) >= _MAX_REDEEM_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many pairing attempts. Wait a few minutes and generate a new code.",
        )


def _record_redeem_failure(key: str) -> None:
    _redeem_failures.setdefault(key, []).append(time.monotonic())


def reset_throttle() -> None:
    """Used by tests; also handy after a deliberate lockout during setup."""
    _redeem_failures.clear()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CredentialPayload(BaseModel):
    """The two ESPN cookies. Write-only: nothing echoes these back."""

    swid: str = Field(min_length=8, max_length=200)
    espn_s2: str = Field(min_length=16, max_length=4000)
    season: int | None = Field(default=None, ge=2000, le=2100)

    model_config = {"extra": "forbid"}

    @field_validator("swid", "espn_s2")
    @classmethod
    def _trim(cls, value: str) -> str:
        value = (value or "").strip().strip('"')
        if not value:
            raise ValueError("This cookie is required.")
        return value


class ExtensionPayload(CredentialPayload):
    """What the browser extension posts.

    `league_id` and `season` are what the extension could read from the ESPN
    page it was invoked on. They are hints -- the league is still confirmed
    against ESPN before anything is stored against the account.
    """

    pairing_code: str = Field(min_length=4, max_length=32)
    league_id: int | None = Field(default=None, ge=1)
    client: str = Field(default="", max_length=80)

    model_config = {"extra": "forbid"}


class SelectLeaguePayload(BaseModel):
    league_id: int = Field(ge=1)
    season: int = Field(ge=2000, le=2100)
    team_id: int | None = Field(default=None, ge=1, le=64)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Status / credentials
# ---------------------------------------------------------------------------


@router.get("/status")
def connection_status(
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Where this account is in the connection flow. No secrets."""
    state = espn_connect.status(session, user, settings)
    state["manual_entry_available"] = True
    return state


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
def submit_credentials(
    payload: CredentialPayload,
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Store this account's ESPN cookies. They are encrypted before they land.

    The response says only that it worked. Returning any part of a credential
    -- even a masked prefix -- would put it back into the browser, which is the
    one place this flow exists to keep it out of.
    """
    try:
        espn_connect.save_credentials(
            session, user, swid=payload.swid, espn_s2=payload.espn_s2, season=payload.season
        )
    except espn_connect.EspnConnectError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    board_service.clear_cache()
    return {"stored": True, "status": espn_connect.status(session, user)}


@router.delete("")
def disconnect(
    session: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    """Disconnect ESPN: delete the stored cookies, league and team.

    Imported league data is intentionally left alone. It contains no
    credentials, and wiping somebody's draft board because they rotated a
    cookie would be a surprising thing for a "disconnect" button to do.
    """
    result = espn_connect.disconnect(session, user)
    board_service.clear_cache()
    return {**result, "status": espn_connect.status(session, user)}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.get("/leagues")
def discover_leagues(
    season: int | None = Query(default=None, ge=2000, le=2100),
    league_id: int | None = Query(default=None, ge=1, description="Also check this league id."),
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Every ESPN football league this account can reach.

    `league_id` lets a manually typed league be confirmed through exactly the
    same path as a discovered one, so the fallback is not a second code path
    that only gets exercised when discovery is already broken.
    """
    try:
        result = espn_connect.discover(
            session,
            user,
            season=season,
            extra_league_ids=[league_id] if league_id else None,
            settings=settings,
        )
    except espn_connect.EspnConnectError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return result.as_dict()


@router.get("/leagues/{league_id}")
def preview_league(
    league_id: int,
    season: int = Query(ge=2000, le=2100),
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """One league in full: rules, roster shape, teams, draft state."""
    try:
        return espn_connect.preview(session, user, league_id, season)
    except espn_connect.EspnConnectError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/select")
def select_league(
    payload: SelectLeaguePayload,
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Choose the league to use, and auto-detect this account's team."""
    try:
        result = espn_connect.select_league(
            session,
            user,
            league_id=payload.league_id,
            season=payload.season,
            team_id=payload.team_id,
        )
    except espn_connect.EspnConnectError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result


@router.post("/import", status_code=status.HTTP_201_CREATED)
def run_import(
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Final step: import the selected league with this account's settings."""
    settings = runtime_config.settings_for_user(session, user)
    if settings.espn_league_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Choose a league before importing.",
        )
    try:
        league = import_league(session, provider=build_provider(settings), settings=settings)
    except EspnConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=redact(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001 - the UI needs the real reason
        log.exception("ESPN import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {redact(exc)}",
        ) from exc

    board_service.clear_cache()
    from sqlalchemy import func, select as sa_select

    player_count = session.scalar(
        sa_select(func.count())
        .select_from(Player)
        .where(Player.season == league.season, Player.source == league.source)
    )
    return {
        "imported": True,
        "players_imported": player_count or 0,
        "league": serialize_league(
            league, board_service.league_scoring(league), board_service.league_shape(league)
        ),
    }


# ---------------------------------------------------------------------------
# Browser extension pairing
# ---------------------------------------------------------------------------


@router.post("/pairing-code", status_code=status.HTTP_201_CREATED)
def create_pairing_code(
    session: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    """A single-use code to type into the browser extension once."""
    espn_connect.purge_expired_codes(session)
    return espn_connect.issue_pairing_code(session, user)


@router.post("/extension/connect", status_code=status.HTTP_201_CREATED)
def connect_from_extension(
    request: Request,
    payload: ExtensionPayload = Body(...),
    session: Session = Depends(get_db),
) -> dict:
    """Accept credentials from the browser extension.

    This is the one endpoint on the router without a session cookie, because
    the extension runs on `espn.com` and has none. The pairing code takes its
    place: single-use, five-minute lifetime, issued only to an already
    signed-in user, and revoked on disconnect.

    The response tells the extension whether it worked and nothing else -- not
    the league name, not the account, nothing that would let a redeemed code
    become a probe for what else is on the server.
    """
    key = _throttle_key(request)
    _check_redeem_throttle(key)

    try:
        user = espn_connect.redeem_pairing_code(
            session, payload.pairing_code, redeemed_by=payload.client or "extension"
        )
    except espn_connect.EspnConnectError as exc:
        _record_redeem_failure(key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    try:
        espn_connect.save_credentials(
            session, user, swid=payload.swid, espn_s2=payload.espn_s2, season=payload.season
        )
    except espn_connect.EspnConnectError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    board_service.clear_cache()

    # The league id the extension read off the page is a hint only. It is
    # stored so the picker can pre-select it, and confirmed against ESPN when
    # the user selects it -- not trusted on arrival.
    hint = None
    if payload.league_id:
        hint = {"league_id": payload.league_id, "season": payload.season}

    return {"stored": True, "league_hint": hint}


@router.get("/extension/manifest-contract")
def extension_contract() -> dict:
    """What a client must send to `/api/espn/extension/connect`.

    Published so the extension and the backend cannot drift apart silently,
    and so the payload shape is testable from both sides.
    """
    return {
        "endpoint": "/api/espn/extension/connect",
        "method": "POST",
        "content_type": "application/json",
        "required": ["pairing_code", "swid", "espn_s2"],
        "optional": ["league_id", "season", "client"],
        "forbidden_extra_fields": True,
        "notes": [
            "Send over HTTPS, or over loopback to a local install.",
            "Never store the cookie values in the extension.",
            "The pairing code is single-use and expires in "
            f"{int(espn_connect.PAIRING_CODE_TTL.total_seconds())} seconds.",
        ],
    }


@router.get("/leagues/{league_id}/imported")
def imported_state(
    league_id: int,
    season: int = Query(ge=2000, le=2100),
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Whether this league is already imported locally."""
    from sqlalchemy import select as sa_select

    league = session.scalars(
        sa_select(League).where(
            League.espn_league_id == league_id, League.season == season
        )
    ).first()
    return {
        "imported": league is not None,
        "imported_at": league.imported_at if league else None,
        "name": league.name if league else "",
    }
