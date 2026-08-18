"""Encrypting stored credentials.

ESPN cookies are not API keys -- they are live session credentials for
somebody's ESPN account. Holding one person's in plaintext on a machine they
control is one thing; holding fifty other people's is another, and that is the
line this module exists to sit on.

The key lives outside the database. A copy of the database alone is then not
enough to read anyone's cookies.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

_KEY_ENV = "FWR_SECRET_KEY"
_KEY_FILENAME = "secret.key"

_fernet: Fernet | None = None


def _key_path() -> Path:
    from ..config import get_settings

    settings = get_settings()
    sqlite_path = settings.sqlite_path()
    directory = sqlite_path.parent if sqlite_path else Path.cwd() / "data"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / _KEY_FILENAME


def _load_key() -> bytes:
    """Prefer an explicit key; otherwise keep a generated one beside the data.

    Generating and persisting is deliberate: the alternative is refusing to
    start until someone sets an environment variable, which in practice means
    the credentials end up stored in plaintext instead.
    """
    from_env = os.environ.get(_KEY_ENV)
    if from_env:
        return from_env.encode("utf-8")

    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()

    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:      # pragma: no cover - Windows permissions differ
        pass
    log.warning(
        "Generated a new encryption key at %s. Back this up: without it, stored "
        "credentials cannot be decrypted.", path,
    )
    return key


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def reset_cipher() -> None:
    """Used by tests that switch data directories between runs."""
    global _fernet
    _fernet = None


def encrypt(value: str | None) -> str:
    if not value:
        return ""
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str | None) -> str:
    """Returns "" for anything unreadable rather than raising.

    A credential encrypted under a key that has since been replaced is simply
    gone; the honest response is to behave as though it was never set and let
    the user enter it again, not to break every request that touches it.
    """
    if not value:
        return ""
    try:
        return _cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        log.warning("A stored credential could not be decrypted; treating it as unset.")
        return ""
