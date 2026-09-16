"""Yahoo OAuth 2.0.

Yahoo has no cookie shortcut.  Reading a Yahoo league means registering an app
at developer.yahoo.com (free, one minute, gives you a client id and secret) and
then a normal three-legged OAuth handshake: send the user to Yahoo, get a code
back, trade it for tokens.

Access tokens last an hour, refresh tokens effectively forever, so the
handshake happens once and everything after it is a silent refresh.  Tokens
are held here and handed to `on_refresh` whenever they change, which is how
they end up in the database without this module knowing SQLAlchemy exists.

Nothing is logged: an access token in a log file is a live credential.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

#: Yahoo's "paste the code back in" flow.  It is the only redirect that works
#: for an app running on someone's laptop with no public HTTPS URL.
OUT_OF_BAND = "oob"

#: Refresh this long before expiry rather than after a 401.
EXPIRY_MARGIN_SECONDS = 120

REQUEST_TIMEOUT = 20.0


class YahooAuthError(RuntimeError):
    """Raised when Yahoo will not issue or refresh a token."""


@dataclass
class YahooTokens:
    access_token: str = ""
    refresh_token: str = ""
    #: Unix timestamp. 0 means "unknown", which is treated as expired.
    expires_at: float = 0.0
    #: Yahoo's id for the signed-in user; identifies which team is theirs.
    guid: str = ""

    @property
    def is_set(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - EXPIRY_MARGIN_SECONDS)

    @classmethod
    def from_response(cls, payload: dict, previous: "YahooTokens | None" = None) -> "YahooTokens":
        previous = previous or cls()
        return cls(
            access_token=str(payload.get("access_token") or ""),
            # A refresh response does not always re-issue the refresh token;
            # dropping it would silently log the user out an hour later.
            refresh_token=str(payload.get("refresh_token") or previous.refresh_token or ""),
            expires_at=time.time() + float(payload.get("expires_in") or 3600),
            guid=str(payload.get("xoauth_yahoo_guid") or previous.guid or ""),
        )


class YahooOAuth:
    """One user's Yahoo authorisation, refreshed on demand."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = OUT_OF_BAND,
        tokens: YahooTokens | None = None,
        on_refresh=None,
    ) -> None:
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.redirect_uri = (redirect_uri or OUT_OF_BAND).strip() or OUT_OF_BAND
        self.tokens = tokens or YahooTokens()
        self._on_refresh = on_refresh

    # -- handshake ---------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def is_authorised(self) -> bool:
        return self.is_configured and self.tokens.is_set

    def authorize_url(self, state: str = "") -> str:
        """Where to send the user to approve access."""
        if not self.is_configured:
            raise YahooAuthError(
                "No Yahoo app configured. Create one at developer.yahoo.com (Fantasy Sports, "
                "Read permission) and paste its Client ID and Client Secret in."
            )
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "language": "en-us",
        }
        if state:
            params["state"] = state
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> YahooTokens:
        """Trade the authorisation code for tokens."""
        code = (code or "").strip()
        if not code:
            raise YahooAuthError("No authorisation code was supplied.")
        payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code": code,
            }
        )
        self._store(YahooTokens.from_response(payload, self.tokens))
        return self.tokens

    def refresh(self) -> YahooTokens:
        if not self.tokens.refresh_token:
            raise YahooAuthError("Not connected to Yahoo yet.")
        payload = self._token_request(
            {
                "grant_type": "refresh_token",
                "redirect_uri": self.redirect_uri,
                "refresh_token": self.tokens.refresh_token,
            }
        )
        self._store(YahooTokens.from_response(payload, self.tokens))
        return self.tokens

    def _token_request(self, data: dict) -> dict:
        try:
            response = httpx.post(
                TOKEN_URL,
                data=data,
                auth=(self.client_id, self.client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise YahooAuthError(f"Could not reach Yahoo: {exc}") from exc

        if response.status_code >= 400:
            raise YahooAuthError(_describe_token_error(response))
        try:
            return response.json()
        except ValueError as exc:
            raise YahooAuthError("Yahoo returned a token response we could not read.") from exc

    def _store(self, tokens: YahooTokens) -> None:
        if not tokens.access_token:
            raise YahooAuthError("Yahoo did not return an access token.")
        self.tokens = tokens
        if self._on_refresh is not None:
            self._on_refresh(tokens)

    # -- authorised requests ----------------------------------------------

    def access_token(self) -> str:
        """A valid access token, refreshing first if this one is stale."""
        if not self.tokens.is_set:
            raise YahooAuthError(
                "Not connected to Yahoo. Open the League screen and connect your account."
            )
        if self.tokens.is_expired:
            self.refresh()
        return self.tokens.access_token

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        """GET with the bearer token, refreshing once if Yahoo rejects it.

        The pre-emptive refresh in `access_token` covers the normal case; this
        retry covers a token Yahoo invalidated early (a password change, an app
        permission edit), which otherwise surfaces as an unexplained 401.
        """
        response = self._get(url, params, self.access_token())
        if response.status_code == 401 and self.tokens.refresh_token:
            log.info("Yahoo rejected the access token; refreshing once.")
            self.refresh()
            response = self._get(url, params, self.tokens.access_token)
        return response

    @staticmethod
    def _get(url: str, params: dict | None, token: str) -> httpx.Response:
        try:
            return httpx.get(
                url,
                params=params or {},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise YahooAuthError(f"Could not reach Yahoo: {exc}") from exc


def _describe_token_error(response: httpx.Response) -> str:
    """Turn Yahoo's token errors into something actionable."""
    detail = ""
    try:
        body = response.json()
        detail = str(body.get("error_description") or body.get("error") or "").strip()
    except ValueError:
        detail = (response.text or "").strip()[:200]

    if response.status_code in (400, 401):
        hint = (
            "Check the Client ID and Client Secret, and that the redirect URI here matches "
            "the one on your Yahoo app exactly."
        )
        if "code" in detail.lower() or "grant" in detail.lower():
            hint = (
                "Authorisation codes are single-use and expire in minutes -- start the "
                "connection again and paste the new code."
            )
        return f"Yahoo rejected the request ({response.status_code}): {detail or 'no detail'}. {hint}"
    return f"Yahoo returned {response.status_code}: {detail or 'no detail'}"
