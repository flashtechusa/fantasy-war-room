"""Passwords, sessions and the current user.

Deliberately small and dependency-free: `hashlib.scrypt` ships with Python, so
there is no password library to keep current. Sessions are stored server-side
rather than encoded into a self-contained token, because the point of the
access model is that switching an account off takes effect immediately.

There is no registration path. Accounts are created by the owner.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuthSession, User, utcnow

#: Cost parameters. n=2**14 keeps a login around a tenth of a second on a small
#: VPS, which is slow enough to make offline guessing expensive and fast enough
#: that nobody notices signing in.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32

SESSION_COOKIE = "fwr_session"
SESSION_DAYS = 30


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Returns False for anything malformed."""
    try:
        scheme, salt_hex, digest_hex = (stored or "").split("$", 2)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.scrypt(
        (password or "").encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=len(expected) or _KEY_LEN,
    )
    return hmac.compare_digest(candidate, expected)


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is UTC.

    Comparing a naive column against an aware `utcnow()` raises, which would
    turn every session check into a 500 rather than a clean expiry.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _hash_token(token: str) -> str:
    """Sessions are stored hashed, so a database copy does not grant access."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(session: Session, user: User, user_agent: str = "") -> str:
    """Returns the raw token; only its hash is persisted."""
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=_hash_token(token),
            user_id=user.id,
            expires_at=utcnow() + timedelta(days=SESSION_DAYS),
            user_agent=(user_agent or "")[:300],
        )
    )
    user.last_login_at = utcnow()
    session.flush()
    return token


def user_for_token(session: Session, token: str | None) -> User | None:
    """The signed-in user, or None. Expired and disabled accounts return None."""
    if not token:
        return None
    row = session.scalars(
        select(AuthSession).where(AuthSession.token_hash == _hash_token(token))
    ).first()
    if row is None:
        return None
    if _as_utc(row.expires_at) <= utcnow():
        session.delete(row)
        session.flush()
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.enabled:
        return None
    return user


def end_session(session: Session, token: str | None) -> None:
    if not token:
        return
    row = session.scalars(
        select(AuthSession).where(AuthSession.token_hash == _hash_token(token))
    ).first()
    if row is not None:
        session.delete(row)
        session.flush()


def revoke_all_for_user(session: Session, user_id: int) -> int:
    """Used when an account is switched off -- expiry alone is too slow."""
    rows = session.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id)
    ).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def ensure_owner(session: Session, username: str, password: str) -> User | None:
    """Create the owner account on first run from configured credentials.

    Without this there is no way in: registration does not exist, so the first
    account has to come from configuration. Does nothing if any user already
    exists, so changing the config later cannot silently reset the password.
    """
    if not username or not password:
        return None
    existing = session.scalars(select(User).limit(1)).first()
    if existing is not None:
        return existing
    user = User(
        username=username.strip().lower(),
        display_name=username.strip(),
        password_hash=hash_password(password),
        role="owner",
        enabled=True,
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalars(
        select(User).where(User.username == (username or "").strip().lower())
    ).first()
    if user is None or not user.enabled:
        # Still run a hash so a missing username and a wrong password take
        # about the same time.
        verify_password(password, "scrypt$00$00")
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
