"""Accounts and sign-in.

Two modes, one code path:

* **Single user** (the default, and every self-hosted install). There is one
  implicit local account, it is signed in automatically, and no password is
  ever set or asked for. Nothing about running this on your own laptop
  changes.
* **Multi user** (`FWR_MULTI_USER=true`, which is what a hosted deployment
  turns on). Accounts have passwords, sessions come from a cookie, and every
  request is scoped to whoever holds it.

Because both modes resolve to "there is a current user", everything downstream
-- connections, leagues, credentials -- is written once and is tenant-safe by
construction rather than by remembering to filter.

Passwords are hashed with scrypt from the standard library: memory-hard, in
Python since 3.6, and no dependency to add or keep patched. Sessions are
stored server-side with only a hash of the cookie value, so revoking one
actually ends it and a stolen database does not hand over live logins.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User, UserSession, utcnow

log = logging.getLogger(__name__)

#: The implicit account a single-user install runs as. Not a routable address,
#: and it carries no password, so it can never be signed in to over the network
#: even if an install is later switched to multi-user.
LOCAL_EMAIL = "local@fantasy-war-room.invalid"

SESSION_COOKIE = "fwr_session"

#: scrypt cost. ~16 MB and a few hundred milliseconds per hash on a laptop,
#: which is the point: it prices out offline guessing.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DK_LEN = 32

MIN_PASSWORD_LENGTH = 8


class AuthError(RuntimeError):
    """Raised when a sign-in or registration cannot proceed."""


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> tuple[str, str]:
    """(hash, salt), both hex."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DK_LEN
    )
    return (digest.hex(), salt.hex())


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Constant-time check. A user with no password can never match."""
    if not password_hash or not salt:
        return False
    try:
        salt_bytes = bytes.fromhex(salt)
    except ValueError:
        return False
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_bytes,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DK_LEN,
    )
    return hmac.compare_digest(digest.hex(), password_hash)


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Passwords need to be at least {MIN_PASSWORD_LENGTH} characters.")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalars(
        select(User).where(User.email == normalise_email(email))
    ).first()


def create_user(
    session: Session, email: str, password: str, display_name: str = ""
) -> User:
    """Register an account. Raises AuthError on a duplicate or weak password."""
    address = normalise_email(email)
    if "@" not in address or "." not in address.split("@")[-1]:
        raise AuthError("That does not look like an email address.")
    validate_password(password)
    if get_user_by_email(session, address) is not None:
        # Deliberately explicit: this is a signup form, where "that address is
        # taken" is the useful answer and enumeration is not the threat that
        # matters. Sign-in stays vague; see `authenticate`.
        raise AuthError("An account with that email already exists.")

    password_hash, salt = hash_password(password)
    # Whoever registers first is whoever stood the server up, so they hold the
    # installation-wide settings. Everyone after them is an ordinary user.
    first_account = not session.scalars(
        select(User).where(User.is_local.is_(False))
    ).first()
    user = User(
        email=address,
        display_name=(display_name or "").strip() or address.split("@")[0],
        password_hash=password_hash,
        password_salt=salt,
        is_admin=first_account,
    )
    session.add(user)
    session.flush()
    log.info("Registered account %s", address)
    return user


def authenticate(session: Session, email: str, password: str) -> User:
    """Check credentials. The failure message never says which half was wrong."""
    user = get_user_by_email(session, email)
    if (
        user is None
        or not user.is_active
        or user.is_local
        or not verify_password(password, user.password_hash, user.password_salt)
    ):
        raise AuthError("Email or password is incorrect.")
    user.last_login_at = utcnow()
    return user


def set_password(session: Session, user: User, password: str) -> None:
    validate_password(password)
    user.password_hash, user.password_salt = hash_password(password)
    session.flush()


def get_or_create_local_user(session: Session) -> User:
    """The account a single-user install runs as, created on first use."""
    user = get_user_by_email(session, LOCAL_EMAIL)
    if user is None:
        user = User(
            email=LOCAL_EMAIL,
            display_name="This computer",
            is_local=True,
            # A single-user install has no one else to be: the person at the
            # keyboard owns the installation.
            is_admin=True,
            password_hash="",
            password_salt="",
        )
        session.add(user)
        session.flush()
    return user


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(
    session: Session, user: User, *, days: int = 30, user_agent: str = ""
) -> str:
    """Create a session and return the raw cookie value, which is never stored."""
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=_hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=max(days, 1)),
            user_agent=(user_agent or "")[:300],
        )
    )
    session.flush()
    return token


def user_for_token(session: Session, token: str) -> User | None:
    """The signed-in user for a cookie value, or None if it is stale or unknown."""
    if not token:
        return None
    row = session.scalars(
        select(UserSession).where(UserSession.token_hash == _hash_token(token))
    ).first()
    if row is None:
        return None

    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        # SQLite hands back naive datetimes; they were written as UTC.
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires <= datetime.now(timezone.utc):
        session.delete(row)
        session.flush()
        return None

    row.last_seen_at = utcnow()
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user


def end_session(session: Session, token: str) -> None:
    if not token:
        return
    row = session.scalars(
        select(UserSession).where(UserSession.token_hash == _hash_token(token))
    ).first()
    if row is not None:
        session.delete(row)
        session.flush()


def end_all_sessions(session: Session, user: User) -> int:
    """Sign a user out everywhere -- used after a password change."""
    rows = session.scalars(
        select(UserSession).where(UserSession.user_id == user.id)
    ).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def purge_expired_sessions(session: Session) -> int:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = session.scalars(select(UserSession).where(UserSession.expires_at <= now)).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def describe_user(user: User) -> dict:
    """Safe-to-return account details. No hashes, ever."""
    return {
        "id": user.id,
        "email": "" if user.is_local else user.email,
        "display_name": user.display_name,
        "is_local": user.is_local,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }
