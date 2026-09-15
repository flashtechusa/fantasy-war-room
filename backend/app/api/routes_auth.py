"""Signing in.

Single-user installs never reach most of this: `FWR_MULTI_USER` is off, the
implicit local account is signed in automatically, and `/api/auth/me` simply
reports who that is. A hosted deployment turns the flag on and these become the
front door.

The session cookie is `HttpOnly` and `SameSite=Lax`, so script on the page
cannot read it and another site cannot ride it. `secure_cookies` keeps it to
HTTPS, which is the default and should only be turned off to test over plain
http on localhost.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import User
from ..services import accounts as account_service
from ..services import connections as connection_service
from ..services.accounts import SESSION_COOKIE, AuthError
from .deps import current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=120)

    model_config = {"extra": "forbid"}


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)

    model_config = {"extra": "forbid"}


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)

    model_config = {"extra": "forbid"}


def _require_multi_user(settings: Settings) -> None:
    if not settings.multi_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This installation runs as a single local account, so there is nothing to "
                "sign in to. Set FWR_MULTI_USER=true to enable accounts."
            ),
        )


def _set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


@router.get("/config")
def read_auth_config() -> dict:
    """What the sign-in screen needs before anyone is signed in.

    Public by necessity: a browser with no session still has to know whether to
    show a login form at all, and whether "create an account" leads anywhere.
    Nothing here is account-specific.
    """
    settings = get_settings()
    return {
        "multi_user": settings.multi_user,
        "allow_registration": settings.multi_user and settings.allow_registration,
    }


@router.get("/me")
def read_me(
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Who is signed in, and what this installation expects of them."""
    settings = get_settings()
    connections = connection_service.list_connections(session, user)
    active = connection_service.active_connection(session, user)
    session.commit()
    return {
        "multi_user": settings.multi_user,
        "allow_registration": settings.allow_registration,
        "user": account_service.describe_user(user),
        "connections": [connection_service.describe(row) for row in connections],
        "active_connection_id": active.id if active else None,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    """Create an account and sign it in."""
    settings = get_settings()
    _require_multi_user(settings)
    if not settings.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This installation is not accepting new accounts.",
        )
    try:
        user = account_service.create_user(
            session, payload.email, payload.password, payload.display_name
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    token = account_service.start_session(
        session,
        user,
        days=settings.session_days,
        user_agent=request.headers.get("user-agent", ""),
    )
    session.commit()
    _set_cookie(response, token, settings)
    return {"user": account_service.describe_user(user), "connections": []}


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    _require_multi_user(settings)
    try:
        user = account_service.authenticate(session, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    token = account_service.start_session(
        session,
        user,
        days=settings.session_days,
        user_agent=request.headers.get("user-agent", ""),
    )
    connections = connection_service.list_connections(session, user)
    session.commit()
    _set_cookie(response, token, settings)
    return {
        "user": account_service.describe_user(user),
        "connections": [connection_service.describe(row) for row in connections],
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    """End this session. Safe to call when not signed in."""
    account_service.end_session(session, request.cookies.get(SESSION_COOKIE, ""))
    session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signed_out": True}


@router.post("/password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Change a password, then sign every other session out."""
    settings = get_settings()
    _require_multi_user(settings)
    if not account_service.verify_password(
        payload.current_password, user.password_hash, user.password_salt
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect.",
        )
    try:
        account_service.set_password(session, user, payload.new_password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # A password change should end sessions elsewhere -- that is usually the
    # reason for changing it -- so revoke them all and issue a fresh one here.
    account_service.end_all_sessions(session, user)
    token = account_service.start_session(
        session,
        user,
        days=settings.session_days,
        user_agent=request.headers.get("user-agent", ""),
    )
    session.commit()
    _set_cookie(response, token, settings)
    return {"changed": True}
