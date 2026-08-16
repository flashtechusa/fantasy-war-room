"""Data provider selection.

`EspnClient` and the demo generator expose the same four methods, so the
importer never needs to know which one it is holding.  That is also what makes
the whole app testable without credentials.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Settings, get_settings
from ..espn import demo
from ..espn.client import EspnClient, EspnConnectionError, PlayerRecord


class DataProvider(Protocol):
    source: str

    def league_settings(self) -> dict: ...

    def teams(self) -> list[dict]: ...

    def draft_results(self) -> list[dict]: ...

    def player_pool(self, limit: int = 600, ppr: bool = False) -> list[PlayerRecord]: ...

    def previous_draft_results(self) -> dict[int, list[dict]]: ...


class DemoProvider:
    """Synthetic league + player pool. See `app.espn.demo` for the caveats."""

    source = "demo"

    def __init__(self, season: int = 2026) -> None:
        self.season = season
        self._pool: list[PlayerRecord] | None = None

    def league_settings(self) -> dict:
        return demo.demo_league_settings(self.season)

    def teams(self) -> list[dict]:
        return demo.demo_teams()

    def draft_results(self) -> list[dict]:
        return []

    def player_pool(self, limit: int = 600, ppr: bool = False) -> list[PlayerRecord]:
        if self._pool is None:
            self._pool = demo.demo_player_pool(self.season)
        return self._pool[:limit]

    def previous_draft_results(self) -> dict[int, list[dict]]:
        return {self.season - 1: demo.demo_previous_draft(self.season - 1, self.player_pool(400))}


class EspnProvider:
    """Adapts EspnClient to the DataProvider protocol."""

    source = "espn"

    def __init__(self, client: EspnClient) -> None:
        self.client = client

    def league_settings(self) -> dict:
        return self.client.league_settings()

    def teams(self) -> list[dict]:
        return self.client.teams()

    def draft_results(self) -> list[dict]:
        return self.client.draft_results()

    def player_pool(self, limit: int = 600, ppr: bool = False) -> list[PlayerRecord]:
        return self.client.player_pool(limit=limit, ppr=ppr)

    def previous_draft_results(self) -> dict[int, list[dict]]:
        return self.client.previous_draft_results()


def build_provider(settings: Settings | None = None) -> DataProvider:
    """Pick the provider for the current configuration.

    Demo mode, or no league id configured, means synthetic data.  Otherwise we
    go to ESPN and let connection errors surface to the caller -- silently
    falling back to fake data during a live draft would be much worse than an
    error message.
    """
    settings = settings or get_settings()
    if settings.demo_mode or settings.espn_league_id is None:
        return DemoProvider(settings.espn_season)
    return EspnProvider(
        EspnClient(
            league_id=settings.espn_league_id,
            season=settings.espn_season,
            swid=settings.espn_swid,
            espn_s2=settings.espn_s2,
        )
    )


def build_espn_client(settings: Settings | None = None) -> EspnClient:
    """Direct ESPN handle (live draft sync). Raises if not configured."""
    settings = settings or get_settings()
    if settings.espn_league_id is None:
        raise EspnConnectionError(
            "No ESPN league configured. Set FWR_ESPN_LEAGUE_ID to enable ESPN sync."
        )
    return EspnClient(
        league_id=settings.espn_league_id,
        season=settings.espn_season,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
    )
