"""Owner-only: who has access, and who is asking for it.

Accounts are created here or not at all -- there is no registration route
anywhere in the app. A new account gets a generated password shown exactly
once, because the alternative is the owner inventing one and sending it
through a chat window.
"""

from __future__ import annotations

import logging
import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BetaRequest, User
from ..services import auth as auth_service
from .routes_auth import require_user

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

ROLES = {"owner", "partner", "client"}

#: No look-alike characters. These get read aloud and typed by hand.
_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in "0OoIl1"
)


def owner_only(user: User = Depends(require_user)) -> User:
    if user.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can manage access.",
        )
    return user


def generate_password(length: int = 14) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class CreateUser(BaseModel):
    username: str = Field(min_length=2, max_length=120)
    display_name: str = Field(default="", max_length=160)
    role: str = Field(default="client")

    model_config = {"extra": "forbid"}


class UpdateUser(BaseModel):
    enabled: bool | None = None
    role: str | None = None

    model_config = {"extra": "forbid"}


def _describe(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "enabled": user.enabled,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


@router.get("/users")
def list_users(
    session: Session = Depends(get_db), _: User = Depends(owner_only)
) -> dict:
    rows = session.scalars(select(User).order_by(User.created_at)).all()
    return {"users": [_describe(u) for u in rows]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUser,
    session: Session = Depends(get_db),
    _: User = Depends(owner_only),
) -> dict:
    username = payload.username.strip().lower()
    if payload.role not in ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Role must be one of {sorted(ROLES)}.",
        )
    existing = session.scalars(select(User).where(User.username == username)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{username}' already has an account.",
        )

    password = generate_password()
    user = User(
        username=username,
        display_name=payload.display_name.strip() or payload.username.strip(),
        password_hash=auth_service.hash_password(password),
        role=payload.role,
        enabled=True,
    )
    session.add(user)
    session.commit()
    log.info("Created account %s (%s)", username, payload.role)
    # The only time this is ever readable. It is not stored in the clear, so
    # it cannot be shown again -- reset it instead.
    return {"user": _describe(user), "password": password}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UpdateUser,
    session: Session = Depends(get_db),
    owner: User = Depends(owner_only),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user.")
    if user.id == owner.id and payload.enabled is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot switch off your own account.",
        )
    if payload.role is not None:
        if payload.role not in ROLES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Role must be one of {sorted(ROLES)}.",
            )
        user.role = payload.role
    if payload.enabled is not None:
        user.enabled = payload.enabled
        if not payload.enabled:
            # Switching an account off has to take effect now, not whenever
            # their session happens to expire.
            auth_service.revoke_all_for_user(session, user.id)
    session.commit()
    return {"user": _describe(user)}


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    session: Session = Depends(get_db),
    _: User = Depends(owner_only),
) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user.")
    password = generate_password()
    user.password_hash = auth_service.hash_password(password)
    # Any browser signed in with the old password is now stale.
    auth_service.revoke_all_for_user(session, user.id)
    session.commit()
    return {"user": _describe(user), "password": password}


@router.patch("/beta-requests/{request_id}")
def mark_request(
    request_id: int,
    handled: bool = True,
    session: Session = Depends(get_db),
    _: User = Depends(owner_only),
) -> dict:
    row = session.get(BetaRequest, request_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such request.")
    row.handled = handled
    session.commit()
    return {"id": row.id, "handled": row.handled}
