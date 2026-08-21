"""Sign in, sign out, and asking for access.

There is no registration endpoint, on purpose. Accounts are created by the
owner; the landing page can only ask.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BetaRequest, User
from ..services import auth as auth_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=400)

    model_config = {"extra": "forbid"}


#: Deliberately permissive. The only thing worth rejecting here is input that
#: obviously is not an address; anything stricter starts refusing real ones,
#: and this is a contact form rather than an authentication path.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=400)
    # Long enough to be worth having, short enough that nobody gives up. The
    # generated ones are 14 characters, so this never rejects one of those.
    new_password: str = Field(min_length=10, max_length=400)

    model_config = {"extra": "forbid"}


class BetaRequestPayload(BaseModel):
    email: str = Field(min_length=3, max_length=250)
    name: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=1000)

    model_config = {"extra": "forbid"}

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, value: str) -> str:
        value = (value or "").strip()
        if not _EMAIL.match(value):
            raise ValueError("That does not look like an email address.")
        return value


def _describe(user: User) -> dict:
    return {
        "username": user.username,
        "display_name": user.display_name or user.username,
        "role": user.role,
        "can_send_trades": bool(getattr(user, "can_send_trades", False)),
    }


def current_user(
    request: Request, session: Session = Depends(get_db)
) -> User | None:
    token = request.cookies.get(auth_service.SESSION_COOKIE)
    return auth_service.user_for_token(session, token)


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
        )
    return user


@router.get("/me")
def me(user: User | None = Depends(current_user)) -> dict:
    """Who is signed in. Always 200 -- the landing page asks this on load."""
    return {
        "authenticated": user is not None,
        "user": _describe(user) if user else None,
    }


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> dict:
    user = auth_service.authenticate(session, payload.username, payload.password)
    if user is None:
        # One message for both cases, so this cannot be used to discover which
        # usernames exist.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That username and password do not match.",
        )

    token = auth_service.create_session(
        session, user, request.headers.get("user-agent", "")
    )
    session.commit()
    response.set_cookie(
        key=auth_service.SESSION_COOKIE,
        value=token,
        max_age=auth_service.SESSION_DAYS * 24 * 3600,
        httponly=True,          # not readable from JavaScript
        secure=True,            # HTTPS only
        samesite="lax",
        path="/",
    )
    log.info("Signed in: %s", user.username)
    return {"authenticated": True, "user": _describe(user)}


@router.post("/logout")
def logout(
    request: Request, response: Response, session: Session = Depends(get_db)
) -> dict:
    auth_service.end_session(session, request.cookies.get(auth_service.SESSION_COOKIE))
    session.commit()
    response.delete_cookie(auth_service.SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.post("/change-password")
def change_password(
    payload: ChangePassword,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    """Replace your own password.

    Requires the current one, so someone who walks up to an unlocked browser
    cannot lock the real owner out of their own account.

    Every other session is ended: if the password is being changed because it
    may have been seen, leaving old sessions alive would defeat the point. The
    browser doing the changing is given a fresh session so it stays signed in.
    """
    if not auth_service.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That is not your current password.",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The new password must be different from the old one.",
        )

    user.password_hash = auth_service.hash_password(payload.new_password)
    auth_service.revoke_all_for_user(session, user.id)
    token = auth_service.create_session(
        session, user, request.headers.get("user-agent", "")
    )
    session.commit()
    response.set_cookie(
        key=auth_service.SESSION_COOKIE,
        value=token,
        max_age=auth_service.SESSION_DAYS * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    log.info("Password changed: %s", user.username)
    return {"changed": True}


@router.post("/beta-request", status_code=status.HTTP_201_CREATED)
def request_beta(
    payload: BetaRequestPayload, session: Session = Depends(get_db)
) -> dict:
    """Public. Stored for the owner to review; there is no self-service signup."""
    existing = session.scalars(
        select(BetaRequest).where(BetaRequest.email == payload.email.lower())
    ).first()
    if existing is None:
        session.add(
            BetaRequest(
                email=payload.email.lower(),
                name=payload.name.strip(),
                note=payload.note.strip(),
            )
        )
        session.commit()
    # Same answer either way -- whether an address already asked is not public.
    return {"received": True}


@router.get("/beta-requests")
def list_beta_requests(
    session: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if user.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner only.")
    rows = session.scalars(
        select(BetaRequest).order_by(BetaRequest.created_at.desc()).limit(200)
    ).all()
    return {
        "requests": [
            {
                "id": r.id,
                "email": r.email,
                "name": r.name,
                "note": r.note,
                "handled": r.handled,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }
