"""The ESPN Connection Assistant.

Connecting an ESPN league used to mean collecting five things by hand: league
id, season, SWID, espn_s2, and which of the teams was yours. Four of them are
derivable once the cookies are present, so this module reduces the job to
"hand us the cookies once, then tap your league".

The flow:

1. `save_credentials` -- cookies arrive once, are encrypted immediately, and
   are never returned by any endpoint afterwards.
2. `discover` -- ESPN tells us which leagues those cookies can reach.
3. `preview` -- one league read in full: rules, teams, draft state.
4. `select_league` -- the choice is stored, and the user's own team is
   identified from the SWID with no typing.
5. `disconnect` -- everything above is deleted.

Manual entry remains a first-class path throughout: `select_league` works
against a league id typed in by hand exactly as it does against a discovered
one, so a user whose fan profile does not list their league is never stuck.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..espn import discovery
from ..espn.discovery import DiscoveredLeague, normalise_swid
from ..espn.http import EspnHttpClient, EspnHttpError
from ..espn.redaction import redact
from ..models import EspnPairingCode, User, UserEspnConfig, utcnow
from . import runtime_config
from . import secrets as secret_store

log = logging.getLogger(__name__)

#: A pairing code is typed by a human into an extension popup, so it is short.
#: The security comes from the five-minute lifetime and single use, not from
#: length -- a code is worthless the moment it is redeemed or expires.
PAIRING_CODE_TTL = timedelta(minutes=5)
PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1
PAIRING_CODE_LENGTH = 8


class EspnConnectError(RuntimeError):
    """A connection step could not be completed. Message is already redacted."""

    def __init__(self, message: str) -> None:
        super().__init__(redact(message))


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _config_for(session: Session, user: User) -> UserEspnConfig | None:
    return session.scalars(
        select(UserEspnConfig).where(UserEspnConfig.user_id == user.id)
    ).first()


def save_credentials(
    session: Session,
    user: User,
    swid: str,
    espn_s2: str,
    season: int | None = None,
) -> UserEspnConfig:
    """Store one user's ESPN cookies, encrypted, and nothing else.

    Deliberately does not set a league: the point of the assistant is that the
    league is discovered next rather than typed. Storing credentials without a
    league is a valid intermediate state.
    """
    swid = normalise_swid(swid)
    espn_s2 = (espn_s2 or "").strip().strip('"')
    if not swid or not espn_s2:
        raise EspnConnectError("Both the SWID and espn_s2 cookies are required.")

    config = _config_for(session, user)
    if config is None:
        config = UserEspnConfig(user_id=user.id)
        session.add(config)

    config.espn_swid_encrypted = secret_store.encrypt(swid)
    config.espn_s2_encrypted = secret_store.encrypt(espn_s2)
    if season is not None:
        config.espn_season = int(season)
    session.flush()
    session.commit()
    # Never log the values, and never the length either -- length is a hint.
    log.info("Stored ESPN credentials for user %s", user.id)
    return config


def has_credentials(session: Session, user: User) -> bool:
    config = _config_for(session, user)
    return bool(config and config.espn_swid_encrypted and config.espn_s2_encrypted)


def credentials(session: Session, user: User) -> tuple[str, str]:
    """Decrypted cookies, for use inside this process only.

    Never call this from a route that serialises its return value.
    """
    config = _config_for(session, user)
    if config is None:
        return ("", "")
    return (
        secret_store.decrypt(config.espn_swid_encrypted),
        secret_store.decrypt(config.espn_s2_encrypted),
    )


def disconnect(session: Session, user: User) -> dict:
    """Delete this user's ESPN connection: credentials, league, team.

    A "disconnect" that left the cookies encrypted-but-present would be a lie.
    The row is removed outright, along with any unredeemed pairing codes -- a
    user disconnecting is exactly the moment an outstanding code should stop
    working.
    """
    config = _config_for(session, user)
    had_credentials = bool(config and (config.espn_swid_encrypted or config.espn_s2_encrypted))
    had_league = bool(config and config.espn_league_id)
    if config is not None:
        session.delete(config)

    codes = session.scalars(
        select(EspnPairingCode).where(EspnPairingCode.user_id == user.id)
    ).all()
    for code in codes:
        session.delete(code)

    session.commit()
    log.info("Disconnected ESPN for user %s", user.id)
    return {
        "disconnected": True,
        "credentials_deleted": had_credentials,
        "league_cleared": had_league,
        "pairing_codes_revoked": len(codes),
    }


def status(session: Session, user: User, settings: Settings | None = None) -> dict:
    """Connection state. Reports whether secrets are set; never returns them."""
    settings = settings or get_settings()
    config = _config_for(session, user)
    resolved = runtime_config.settings_for_user(session, user, settings)
    return {
        "connected": bool(config and config.espn_league_id),
        "credentials_stored": bool(
            config and config.espn_swid_encrypted and config.espn_s2_encrypted
        ),
        "swid_set": bool(config and config.espn_swid_encrypted),
        "espn_s2_set": bool(config and config.espn_s2_encrypted),
        "espn_league_id": config.espn_league_id if config else None,
        "espn_season": (config.espn_season if config else None) or resolved.espn_season,
        "my_team_id": config.my_team_id if config else None,
        "updated_at": config.updated_at if config else None,
        "can_discover": bool(
            config and config.espn_swid_encrypted and config.espn_s2_encrypted
        ),
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    leagues: list[DiscoveredLeague]
    warnings: list[str]
    season: int

    def as_dict(self) -> dict:
        return {
            "season": self.season,
            "count": len(self.leagues),
            "leagues": [lg.summary() for lg in self.leagues],
            "warnings": self.warnings,
        }


def http_client_for(
    session: Session, user: User, require_credentials: bool = True
) -> EspnHttpClient:
    """An ESPN handle for this user, with or without their cookies.

    `require_credentials=False` returns an anonymous client when none are
    stored. That is not a degraded mode -- a *public* league answers ESPN's v3
    endpoint with no cookies at all, which is the only route that works on a
    phone, where no browser will surrender an HttpOnly cookie. Discovery still
    needs credentials (it is an account-level lookup), so an anonymous client
    can read a league but never find one.
    """
    swid, espn_s2 = credentials(session, user)
    if swid and espn_s2:
        return EspnHttpClient(swid=swid, espn_s2=espn_s2)
    if require_credentials:
        raise EspnConnectError(
            "No ESPN credentials stored for this account. Connect ESPN first."
        )
    return EspnHttpClient()


def discover(
    session: Session,
    user: User,
    season: int | None = None,
    extra_league_ids: list[int] | None = None,
    client: EspnHttpClient | None = None,
    settings: Settings | None = None,
) -> DiscoveryResult:
    """Every ESPN football league reachable for one season.

    With credentials stored, that means every league on the account. Without
    them it means whichever league ids the caller names, and only if they are
    public -- which is the whole path for someone on a phone.
    """
    settings = settings or get_settings()
    season = int(season or runtime_config.settings_for_user(session, user, settings).espn_season)
    owned = client is None
    # Naming a league id is a request to look that one up, so credentials are
    # not required for it. Asking "what leagues do I have?" genuinely is.
    client = client or http_client_for(
        session, user, require_credentials=not extra_league_ids
    )
    anonymous = not client.has_credentials
    try:
        leagues, warnings = discovery.discover_leagues(
            client, season=season, extra_league_ids=extra_league_ids
        )
    except EspnHttpError as exc:
        raise EspnConnectError(str(exc)) from exc
    finally:
        if owned:
            client.close()

    if anonymous:
        # The fan-profile failure is expected here and its message ("a SWID is
        # required") would read as a bug rather than as the intended path.
        warnings = [
            "Checked without ESPN cookies, so only public leagues are visible. "
            "Connect your ESPN account from a desktop browser to see private ones."
        ]
    elif not leagues and not warnings:
        warnings.append(
            f"ESPN reported no fantasy football leagues for {season} on this account. "
            "If you know the league id you can still enter it by hand."
        )
    return DiscoveryResult(leagues=leagues, warnings=warnings, season=season)


def preview(
    session: Session,
    user: User,
    league_id: int,
    season: int,
    client: EspnHttpClient | None = None,
) -> dict:
    """One league read in full, for the "verify the rules" step.

    Works without credentials when the league is public, so the phone path can
    confirm the rules before importing exactly as the desktop path does.
    """
    owned = client is None
    client = client or http_client_for(session, user, require_credentials=False)
    try:
        league = discovery.league_preview(client, int(league_id), int(season))
    except EspnHttpError as exc:
        raise EspnConnectError(str(exc)) from exc
    finally:
        if owned:
            client.close()

    return {
        "league": league.summary(),
        "rules": discovery.rules_summary(league),
    }


def select_league(
    session: Session,
    user: User,
    league_id: int,
    season: int,
    team_id: int | None = None,
    client: EspnHttpClient | None = None,
) -> dict:
    """Point this account at one league, auto-detecting its team.

    `team_id` is only needed when the SWID does not match any owner -- which
    happens in leagues where somebody else set the team up. Passing it wins
    over detection, because an explicit choice always should. A public league
    connected without cookies has no SWID to match, so the team is always
    chosen by hand there.
    """
    owned = client is None
    client = client or http_client_for(session, user, require_credentials=False)
    try:
        league = discovery.league_preview(client, int(league_id), int(season))
    except EspnHttpError as exc:
        raise EspnConnectError(str(exc)) from exc
    finally:
        if owned:
            client.close()

    resolved_team = int(team_id) if team_id is not None else league.my_team_id
    if resolved_team is not None and not any(
        t["espn_team_id"] == resolved_team for t in league.teams
    ):
        raise EspnConnectError(
            f"Team {resolved_team} is not in league {league_id} for {season}."
        )

    config = _config_for(session, user)
    if config is None:
        config = UserEspnConfig(user_id=user.id)
        session.add(config)
    config.espn_league_id = int(league_id)
    config.espn_season = int(season)
    config.my_team_id = resolved_team
    session.flush()
    session.commit()

    from . import board as board_service

    board_service.clear_cache()

    return {
        "selected": True,
        "league": league.summary(),
        "rules": discovery.rules_summary(league),
        "my_team_id": resolved_team,
        "team_auto_detected": team_id is None and league.my_team_id is not None,
    }


# ---------------------------------------------------------------------------
# Pairing codes (browser extension)
# ---------------------------------------------------------------------------


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def generate_code() -> str:
    return "".join(secrets.choice(PAIRING_CODE_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def issue_pairing_code(session: Session, user: User) -> dict:
    """Mint a single-use code the extension can trade for a connection.

    Any code this user already holds is revoked first: two live codes for one
    account is one more than anybody needs, and the older one is invariably the
    forgotten one.
    """
    for existing in session.scalars(
        select(EspnPairingCode).where(
            EspnPairingCode.user_id == user.id, EspnPairingCode.used_at.is_(None)
        )
    ).all():
        session.delete(existing)

    code = generate_code()
    expires = utcnow() + PAIRING_CODE_TTL
    session.add(
        EspnPairingCode(code_hash=_hash_code(code), user_id=user.id, expires_at=expires)
    )
    session.commit()
    # The plaintext code is returned exactly once, to the signed-in user who
    # asked for it, and is never written to a log.
    return {
        "code": code,
        "expires_at": expires,
        "expires_in_seconds": int(PAIRING_CODE_TTL.total_seconds()),
    }


def redeem_pairing_code(session: Session, code: str, redeemed_by: str = "") -> User:
    """Resolve a code to its user and burn it. Raises if it is not usable."""
    row = session.scalars(
        select(EspnPairingCode).where(EspnPairingCode.code_hash == _hash_code(code or ""))
    ).first()
    # One message for every failure mode. Distinguishing "expired" from
    # "wrong" would tell somebody guessing codes when they had guessed one.
    if row is None or row.used_at is not None:
        raise EspnConnectError("That pairing code is not valid. Generate a new one.")

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        # SQLite hands back naive datetimes; compare like with like.
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
    if expires_at < utcnow():
        raise EspnConnectError("That pairing code is not valid. Generate a new one.")

    user = session.get(User, row.user_id)
    if user is None or not user.enabled:
        raise EspnConnectError("That pairing code is not valid. Generate a new one.")

    row.used_at = utcnow()
    row.redeemed_by = (redeemed_by or "")[:120]
    session.commit()
    return user


def purge_expired_codes(session: Session) -> int:
    """Housekeeping. Expired codes are useless; keeping them is just risk."""
    rows = session.scalars(
        select(EspnPairingCode).where(EspnPairingCode.expires_at < utcnow())
    ).all()
    for row in rows:
        session.delete(row)
    if rows:
        session.commit()
    return len(rows)
