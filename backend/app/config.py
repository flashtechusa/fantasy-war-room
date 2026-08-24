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
        validation_alias=AliasChoices("FWR_ESPN_SEASON", "ESPN_SEASON", "ESPN_YEAR"),
    )
    espn_swid: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ESPN_SWID", "ESPN_SWID", "SWID"),
    )
    espn_s2: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ESPN_S2", "ESPN_S2", "ESPN_COOKIE_S2"),
    )

    # --- Accounts ---------------------------------------------------------
    #: Bootstraps the first account. There is no registration path, so without
    #: this there would be no way in. Only used when no user exists yet, so
    #: changing it later cannot silently reset a password.
    admin_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ADMIN_USERNAME", "ADMIN_USERNAME"),
    )
    admin_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_ADMIN_PASSWORD", "ADMIN_PASSWORD"),
    )

    # --- Additional projection sources ------------------------------------
    #: Bring your own key. Nothing is bundled, and the FantasyPros source stays
    #: inert until this is set. Free keys are issued for personal,
    #: non-commercial use, so whether a deployment may use it is a question for
    #: whoever holds the key -- hence per-install and off by default.
    fantasypros_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FWR_FANTASYPROS_API_KEY", "FANTASYPROS_API_KEY"),
    )

    #: Compare Sleeper's projections against the existing source. When on, the
    #: board is built from Sleeper's raw component projections re-scored under
    #: this league's rules -- used *exclusively*, never blended with ESPN or
    #: FantasyPros. Off by default, and off preserves current behaviour exactly.
    #: Resolved per user (see `runtime_config.settings_for_user`). Superseded by
    #: `projection_mode`; retained for backward compatibility.
    use_sleeper_projections: bool = False

    #: Which projection source builds the board for the signed-in user. One of
    #: "espn" (native, the default -- byte-identical to before this existed),
    #: "sleeper", "fantasypros", or "consensus". Resolved per user.
    projection_mode: str = "espn"

    #: Install-wide master switch (kill switch) for sending trade proposals to
    #: ESPN. OFF by default: no account can send until the owner turns it on, and
    #: turning it off again immediately blocks all further sends. This is in
    #: addition to the per-user `can_send_trades` capability and the per-send
    #: confirmation -- all three must line up for a proposal to leave the app.
    trades_send_enabled: bool = False

    #: Install-wide master switch for Auto Mode (autonomous team management). OFF
    #: by default; no account's autopilot runs until the owner turns it on, and
    #: turning it off stops every account's Auto Mode immediately. In addition to
    #: the per-user `can_auto_mode` capability and the user's own opt-in.
    auto_mode_enabled: bool = False

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

    #: Which ESPN path supplies live draft picks.
    #: `auto`      -- consult both and take whichever reports more picks
    #: `espn_api`  -- the library only (the behaviour before the fallback existed)
    #: `direct`    -- the `mDraftDetail` endpoint only
    espn_draft_source: str = "auto"

    #: Turns on the ESPN Draft Sync Diagnostics screen. Off by default: it is a
    #: testing tool, not a feature, and it should not be one more thing a user
    #: has to understand. The endpoint behind it stays available either way,
    #: because during a draft you want it without a restart.
    debug_screens: bool = False

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

    @field_validator("espn_draft_source")
    @classmethod
    def _known_draft_source(cls, v: str) -> str:
        """An unknown value falls back to `auto` rather than refusing to boot.

        This setting exists so a live draft can be steered onto one path if the
        other misbehaves. A typo in it should not be the reason the app will
        not start ten minutes before a draft.
        """
        value = (v or "auto").strip().lower()
        return value if value in {"auto", "espn_api", "direct"} else "auto"

    @field_validator("espn_s2", "my_team_name")
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
