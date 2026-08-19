"""Data provider selection.

`EspnClient` and the demo generator expose the same four methods, so the
importer never needs to know which one it is holding.  That is also what makes
the whole app testable without credentials.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Settings, get_settings
from ..espn import demo
from ..espn.client import (
    EspnClient,
    EspnConnectionError,
    EspnNotConfigured,
    PlayerRecord,
)
from ..espn.http import EspnHttpClient


class DataProvider(Protocol):
    source: str

    def league_settings(self) -> dict: ...

    def teams(self) -> list[dict]: ...

    def draft_results(self) -> list[dict]: ...

    def player_pool(
        self,
        limit: int = 600,
        ppr: bool = False,
        week: int | None = None,
        free_agents_only: bool = False,
    ) -> list[PlayerRecord]: ...

    def previous_draft_results(self) -> dict[int, list[dict]]: ...

    @property
    def current_week(self) -> int: ...


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

    def player_pool(
        self,
        limit: int = 600,
        ppr: bool = False,
        week: int | None = None,
        free_agents_only: bool = False,
    ) -> list[PlayerRecord]:
        if self._pool is None:
            self._pool = demo.demo_player_pool(self.season)
        pool = self._pool
        if free_agents_only:
            pool = [p for p in pool if p.availability in {"FREEAGENT", "WAIVERS"}]
        return pool[:limit]

    def previous_draft_results(self) -> dict[int, list[dict]]:
        return {self.season - 1: demo.demo_previous_draft(self.season - 1, self.player_pool(400))}

    @property
    def current_week(self) -> int:
        return demo.DEMO_CURRENT_WEEK


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

    def player_pool(
        self,
        limit: int = 600,
        ppr: bool = False,
        week: int | None = None,
        free_agents_only: bool = False,
    ) -> list[PlayerRecord]:
        return self.client.player_pool(
            limit=limit, ppr=ppr, week=week, free_agents_only=free_agents_only
        )

    def previous_draft_results(self) -> dict[int, list[dict]]:
        return self.client.previous_draft_results()

    @property
    def current_week(self) -> int:
        return self.client.current_week


def build_provider(settings: Settings | None = None) -> DataProvider:
    """Pick the provider for the current configuration.

    Only an explicit demo mode gets synthetic data. "No league configured"
    used to land here too, which meant a signed-in account that had not
    connected ESPN yet imported 330 fabricated players -- into a pool keyed by
    season and shared with every real league in it. One curious click by a
    client account moved the bar under all twelve teams in the owner's league.

    Not knowing which league to import is an error, and it says so.
    """
    settings = settings or get_settings()
    if settings.demo_mode:
        return DemoProvider(settings.espn_season)
    if settings.espn_league_id is None:
        raise EspnNotConfigured(
            "No ESPN league is connected to this account. Add your league on "
            "the League screen, or use Connect ESPN to find it, before importing."
        )
    return EspnProvider(
        EspnClient(
            league_id=settings.espn_league_id,
            season=settings.espn_season,
            swid=settings.espn_swid,
            espn_s2=settings.espn_s2,
            draft_source=settings.espn_draft_source,
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
        draft_source=settings.espn_draft_source,
    )


def build_http_client(settings: Settings | None = None) -> EspnHttpClient:
    """A direct ESPN handle for discovery, which happens before a league exists.

    Unlike `build_espn_client` this needs no league id -- finding the league is
    the whole point.
    """
    settings = settings or get_settings()
    return EspnHttpClient(swid=settings.espn_swid, espn_s2=settings.espn_s2)
