"""Application configuration.

Every setting is sourced from the environment (or a local `.env`) with the
`FWR_` prefix.  Credentials are never hard-coded and never logged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    espn_league_id: int | None = None
    espn_season: int = 2026
    espn_swid: str | None = None
    espn_s2: str | None = None

    # --- Draft ------------------------------------------------------------
    my_team_id: int | None = None
    my_team_name: str | None = None
    my_draft_slot: int | None = None

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
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that patch the environment."""
    get_settings.cache_clear()
