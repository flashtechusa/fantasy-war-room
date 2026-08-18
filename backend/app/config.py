"""Application configuration.

Every setting is sourced from the environment (or a local `.env`) with the
`FWR_` prefix.  Credentials are never hard-coded and never logged.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FWR_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # Fields carrying a `validation_alias` are otherwise settable *only* by
        # that alias, which quietly broke `platform` when the runtime overlay
        # re-validated a dump keyed by field name. Most fields got away with it
        # because their aliases match the field name case-insensitively; that
        # is luck, not design.
        populate_by_name=True,
    )

    # --- ESPN -------------------------------------------------------------
    # Accept the `FWR_`-prefixed names *and* the bare `ESPN_*` names, because
    # the bare ones are what ESPN tooling and every forum post use -- pasting
    # them straight in should just work.  `ESPN_YEAR` is accepted as a synonym
    # for the season for the same reason.
    espn_league_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ESPN_LEAGUE_ID", "ESPN_LEAGUE_ID"),
    )
    espn_season: int = Field(
        default=2026,
        validation_alias=AliasChoices(
            "FWR_ESPN_SEASON", "FWR_SEASON", "ESPN_SEASON", "ESPN_YEAR", "SEASON"
        ),
    )
    espn_swid: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ESPN_SWID", "ESPN_SWID", "SWID"),
    )
    espn_s2: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ESPN_S2", "ESPN_S2", "ESPN_COOKIE_S2"),
    )

    # --- Platform ---------------------------------------------------------
    #: Which fantasy platform this install reads: `espn` or `yahoo`.
    #:
    #: The two are alternatives, not a blend -- a league lives on one of them --
    #: so this picks which set of credentials below is used. Everything past the
    #: import is platform-agnostic.
    platform: str = Field(
        default="espn",
        validation_alias=AliasChoices("FWR_PLATFORM", "FANTASY_PLATFORM"),
    )

    # --- Yahoo ------------------------------------------------------------
    # Yahoo has no cookie shortcut: reading a league needs an app registered at
    # developer.yahoo.com (free) plus a one-time OAuth handshake. The tokens
    # that come out of it are stored like any other credential -- in `.env` or
    # in the local database -- and are never returned by the API.
    yahoo_league_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_YAHOO_LEAGUE_ID", "YAHOO_LEAGUE_ID"),
    )
    yahoo_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FWR_YAHOO_CLIENT_ID", "YAHOO_CLIENT_ID", "YAHOO_CONSUMER_KEY"
        ),
    )
    yahoo_client_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FWR_YAHOO_CLIENT_SECRET", "YAHOO_CLIENT_SECRET", "YAHOO_CONSUMER_SECRET"
        ),
    )
    #: `oob` means Yahoo shows a code to paste back in, which is what works for
    #: an app running on a laptop with no public HTTPS address.
    yahoo_redirect_uri: str = Field(
        default="oob",
        validation_alias=AliasChoices("FWR_YAHOO_REDIRECT_URI", "YAHOO_REDIRECT_URI"),
    )
    yahoo_access_token: str | None = None
    yahoo_refresh_token: str | None = None
    #: Unix timestamp the access token expires at.
    yahoo_token_expires: float = 0.0
    #: Yahoo's id for the connected account.
    yahoo_guid: str | None = None

    # --- Additional projection sources ------------------------------------
    #: Bring your own key. Nothing is bundled, and the FantasyPros source stays
    #: inert until this is set. Free keys are issued for personal,
    #: non-commercial use, so whether a deployment may use it is a question for
    #: whoever holds the key -- hence per-install and off by default.
    fantasypros_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_FANTASYPROS_API_KEY", "FANTASYPROS_API_KEY"),
    )

    # --- Draft ------------------------------------------------------------
    my_team_id: int | None = None
    my_team_name: str | None = None
    my_draft_slot: int | None = None
    #: FAAB you have left. Only meaningful in money-waiver leagues; ESPN does
    #: not report remaining budget reliably, so it is entered in the app.
    faab_remaining: int | None = None

    # --- App --------------------------------------------------------------
    database_url: str = "sqlite:///./data/fantasy_war_room.db"
    demo_mode: bool = False
    player_cache_ttl: int = 900
    draft_poll_interval: int = 10

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Size of the draftable player pool pulled from ESPN.
    player_pool_size: int = Field(default=600, ge=50, le=2000)

    @field_validator("espn_swid")
    @classmethod
    def _normalise_swid(cls, v: str | None) -> str | None:
        """ESPN's SWID cookie is brace-wrapped; accept it with or without."""
        if not v:
            return None
        v = v.strip().strip('"')
        if not v:
            return None
        if not v.startswith("{"):
            v = "{" + v
        if not v.endswith("}"):
            v = v + "}"
        return v

    @field_validator("platform")
    @classmethod
    def _normalise_platform(cls, v: str) -> str:
        value = (v or "").strip().lower()
        return value if value in {"espn", "yahoo"} else "espn"

    @field_validator(
        "espn_s2",
        "my_team_name",
        "yahoo_client_id",
        "yahoo_client_secret",
        "yahoo_access_token",
        "yahoo_refresh_token",
        "yahoo_guid",
    )
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().strip('"')
        return v or None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_espn_credentials(self) -> bool:
        """True when private-league cookies are present."""
        return bool(self.espn_swid and self.espn_s2)

    @property
    def can_reach_espn(self) -> bool:
        """A league id is the minimum needed to try a (public) league."""
        return not self.demo_mode and self.espn_league_id is not None

    @property
    def is_yahoo(self) -> bool:
        return self.platform == "yahoo"

    @property
    def has_yahoo_app(self) -> bool:
        """True once a Yahoo developer app has been configured."""
        return bool(self.yahoo_client_id and self.yahoo_client_secret)

    @property
    def has_yahoo_credentials(self) -> bool:
        """True once the OAuth handshake has completed."""
        return bool(self.yahoo_access_token and self.yahoo_refresh_token)

    @property
    def can_reach_yahoo(self) -> bool:
        return (
            not self.demo_mode
            and self.yahoo_league_id is not None
            and self.has_yahoo_app
            and self.has_yahoo_credentials
        )

    def sqlite_path(self) -> Path | None:
        if not self.database_url.startswith("sqlite"):
            return None
        raw = self.database_url.split("///", 1)[-1]
        path = Path(raw)
        return path if path.is_absolute() else (REPO_ROOT / path)


@lru_cache
def get_settings() -> Settings:
    """Resolve configuration.

    `FWR_ENV_FILE` overrides which env file is read -- point it at a second
    file to keep configs for more than one league side by side, or set it to
    an empty string to ignore `.env` entirely (which is what the test suite
    does, so a developer's real credentials can never leak into a test run).
    """
    override = os.environ.get("FWR_ENV_FILE")
    if override is not None:
        return Settings(_env_file=override or None)
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that patch the environment."""
    get_settings.cache_clear()
