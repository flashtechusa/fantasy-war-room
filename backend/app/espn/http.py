"""A direct, minimal client for ESPN's v3 fantasy API.

Why this exists alongside `espn-api`
------------------------------------
`espn-api` remains the primary path for importing a league: it is battle-tested
and it already knows the awkward parts of ESPN's payloads. But three things it
cannot do are exactly the things this project needs most:

1. **League discovery.** `espn-api` is constructed *with* a league id. Finding
   out which leagues a set of cookies can reach happens on a different ESPN
   host entirely, so it has to be its own request.
2. **Live draft polling.** `espn-api` refuses to return picks until ESPN marks
   the draft complete, which is precisely the window we care about. Reading
   `mDraftDetail` ourselves lets us see picks while the draft is running.
3. **Observability.** We need per-request latency and error detail to answer
   "is ESPN keeping up with the draft?", and `espn-api` does not surface it.

Everything here is written from ESPN's public request shapes -- the same URL
and `view=` conventions any browser session uses. Nothing is borrowed from a
copyleft-licensed client.

Security note: cookies are only ever attached as a request header, never
logged, never echoed back, and every error message goes through `redact()`
before it is allowed to escape this module.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from .redaction import redact, redact_url

log = logging.getLogger(__name__)

#: The read-only mirror ESPN's own web app talks to. The older
#: `fantasy.espn.com` host still answers, but this one is what current clients
#: use and it is markedly less aggressive about rate limiting.
FANTASY_READ_HOST = "https://lm-api-reads.fantasy.espn.com"

#: v3 league endpoint for 2018 and later.
LEAGUE_PATH = "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"

#: Pre-2018 seasons live behind a different route that takes the season as a
#: query parameter and answers with a single-element array.
LEAGUE_HISTORY_PATH = "/apis/v3/games/ffl/leagueHistory/{league_id}"

#: The "fan" profile: everything one ESPN account follows, including every
#: fantasy team it manages. This is the only ESPN surface that maps a set of
#: cookies to a list of leagues, which makes it the whole basis of discovery.
FAN_HOST = "https://fan.api.espn.com"
FAN_PATH = "/apis/v2/fans/{swid}"

#: The first season served by the v3 league route.
FIRST_V3_SEASON = 2018

DEFAULT_TIMEOUT = 12.0


class EspnHttpError(RuntimeError):
    """An ESPN request failed. The message is always already redacted."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(redact(message))
        self.status_code = status_code


@dataclass
class EspnResponse:
    """One ESPN response plus the timing we need for draft diagnostics."""

    payload: Any
    status_code: int
    latency_ms: float
    #: Already redacted; safe to store and to return from the API.
    url: str
    views: list[str] = field(default_factory=list)

    @property
    def data(self) -> dict:
        """ESPN's history route answers with a one-element list. Normalise it."""
        if isinstance(self.payload, list):
            return self.payload[0] if self.payload else {}
        return self.payload if isinstance(self.payload, dict) else {}


class EspnHttpClient:
    """Authenticated, low-level access to ESPN's fantasy football API.

    One instance per set of credentials. Reusing it reuses the underlying
    connection pool, which matters when polling a live draft.
    """

    def __init__(
        self,
        swid: str | None = None,
        espn_s2: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.swid = (swid or "").strip() or None
        self.espn_s2 = (espn_s2 or "").strip() or None
        self.timeout = timeout
        self._transport = transport
        self._client: httpx.Client | None = None

    # -- plumbing ----------------------------------------------------------

    @property
    def has_credentials(self) -> bool:
        return bool(self.swid and self.espn_s2)

    def _cookie_header(self) -> dict[str, str]:
        """The auth header. Never logged, never returned to a caller."""
        if not self.has_credentials:
            return {}
        return {"Cookie": f"espn_s2={self.espn_s2}; SWID={self.swid};"}

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                transport=self._transport,
                follow_redirects=True,
                headers={
                    # ESPN answers anonymous-looking clients inconsistently.
                    "Accept": "application/json",
                    "User-Agent": "FantasyWarRoom/1.0",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "EspnHttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def get(
        self,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        views: list[str] | None = None,
    ) -> EspnResponse:
        """One GET, timed, with cookies attached and every failure redacted."""
        request_headers = {**self._cookie_header(), **(headers or {})}
        safe_url = redact_url(url)
        started = time.perf_counter()
        try:
            response = self._http().get(url, params=params, headers=request_headers)
        except httpx.TimeoutException as exc:
            raise EspnHttpError(f"ESPN timed out after {self.timeout:.0f}s: {exc}") from exc
        except httpx.HTTPError as exc:
            raise EspnHttpError(f"Could not reach ESPN: {exc}") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        self._raise_for_status(response.status_code)

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise EspnHttpError(
                f"ESPN returned a non-JSON response ({response.status_code}): {exc}"
            ) from exc

        return EspnResponse(
            payload=payload,
            status_code=response.status_code,
            latency_ms=round(latency_ms, 1),
            url=safe_url,
            views=list(views or []),
        )

    def _raise_for_status(self, status: int) -> None:
        if status == 200:
            return
        if status in (401, 403):
            hint = (
                "The stored SWID/espn_s2 cookies were rejected -- they expire when you "
                "sign out of ESPN. Reconnect your ESPN account."
                if self.has_credentials
                else "This league is private. Connect your ESPN account to read it."
            )
            raise EspnHttpError(f"ESPN denied access ({status}). {hint}", status_code=status)
        if status == 404:
            raise EspnHttpError(
                "ESPN has no such league for that season (404).", status_code=status
            )
        if status == 429:
            raise EspnHttpError(
                "ESPN is rate limiting this connection (429). Slow the poll interval down.",
                status_code=status,
            )
        raise EspnHttpError(f"ESPN returned HTTP {status}.", status_code=status)

    # -- endpoints ---------------------------------------------------------

    @staticmethod
    def league_url(league_id: int, season: int) -> str:
        """The right league URL for the season -- v3 or the history route."""
        if int(season) < FIRST_V3_SEASON:
            return FANTASY_READ_HOST + LEAGUE_HISTORY_PATH.format(league_id=int(league_id))
        return FANTASY_READ_HOST + LEAGUE_PATH.format(
            season=int(season), league_id=int(league_id)
        )

    def league_view(
        self,
        league_id: int,
        season: int,
        views: list[str] | str,
        scoring_period: int | None = None,
        extra_params: dict | None = None,
        player_filter: dict | None = None,
    ) -> EspnResponse:
        """GET one league with one or more `view=` parameters.

        ESPN composes views: asking for `mSettings` and `mTeam` in a single
        request returns both blobs, which is how the connection assistant reads
        a whole league in one round trip.
        """
        view_list = [views] if isinstance(views, str) else list(views)
        params: dict[str, Any] = {"view": view_list}
        if int(season) < FIRST_V3_SEASON:
            params["seasonId"] = int(season)
        if scoring_period is not None:
            params["scoringPeriodId"] = int(scoring_period)
        if extra_params:
            params.update(extra_params)

        headers = {}
        if player_filter is not None:
            headers["x-fantasy-filter"] = json.dumps(player_filter)

        return self.get(
            self.league_url(league_id, season),
            params=params,
            headers=headers,
            views=view_list,
        )

    def draft_detail(self, league_id: int, season: int) -> EspnResponse:
        """`view=mDraftDetail` -- the draft board, including a running draft.

        This is the endpoint the live-draft fallback polls. It is small (a few
        hundred bytes per pick, no player payload) which is what makes it
        cheap enough to poll every few seconds.
        """
        return self.league_view(league_id, season, "mDraftDetail")

    def fan_profile(self, swid: str | None = None) -> EspnResponse:
        """Everything one ESPN account follows, including its fantasy teams.

        The SWID goes in the path, so `redact_url` matters here: an unredacted
        error from this call would contain a credential.
        """
        identifier = (swid or self.swid or "").strip()
        if not identifier:
            raise EspnHttpError("A SWID is required to look up ESPN leagues.")
        if not identifier.startswith("{"):
            identifier = "{" + identifier
        if not identifier.endswith("}"):
            identifier = identifier + "}"

        # The braces are not URL-safe; ESPN expects them percent-encoded.
        url = FAN_HOST + FAN_PATH.format(swid=quote(identifier, safe=""))
        # `featureFlags` is what makes ESPN include fantasy entries in the
        # response; without it the profile comes back with news preferences
        # only. The rest keeps the payload as small as ESPN allows.
        params = {
            "featureFlags": [
                "expandAthlete",
                "isolateEvents",
                "challengeEntries",
                "openInAppKey",
            ],
            "showAirings": "buy,live,replay",
            "source": "fantasy-war-room",
            "lang": "en",
            "section": "espn",
            "region": "us",
        }
        return self.get(url, params=params, views=["fan"])
