"""Disney OneID -- the passwordless (OTP) path to an ESPN session.

WHAT THIS IS, AND IS NOT
------------------------
This drives Disney's OneID *account-recovery* flow, which authenticates with a
one-time code emailed to the user instead of a password. The user types their
ESPN email, Disney sends a six-digit code, and redeeming that code yields an
authenticated ESPN session.

It is **only an acquisition mechanism.** The single job of this module is to end
up holding ESPN's two session cookies -- `SWID` and `espn_s2` -- and nothing
else. The moment those two are in hand, every Disney artifact (the access token,
the refresh token, the recovery context, the device grant, the OTP itself) is
discarded. The rest of the app then uses the existing ESPN connection layer,
which already understands exactly those two cookies. There is deliberately no
second authentication architecture downstream of this.

THE CONTRACT IS NOT YET VERIFIED AGAINST LIVE DISNEY
----------------------------------------------------
The public description of this flow gives the endpoint URLs but not their
request/response bodies, and this environment cannot reach `registerdisney`.
Every place where a request body is built or a field is read from a response is
therefore a best guess, marked `# CONTRACT:`. `scripts/test_espn_otp.py` runs
the flow against real Disney and prints enough (redacted) structure to correct
these in one pass. Until it has, treat the field paths here as provisional.

SECURITY
--------
* The ESPN password is never collected -- there is no password in an OTP flow.
* The OTP, the Disney tokens, and the recovery context never touch the database
  and are never logged.
* Raw Disney responses are never logged. Errors are redacted (`redact()` also
  scrubs email addresses) before they leave this module.
* The only thing that persists is `SWID` + `espn_s2`, through the existing
  encrypted store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .redaction import redact

log = logging.getLogger(__name__)

#: The OneID client the ESPN *website* identifies as. The recovery flow is
#: scoped to this client id.
CLIENT_ID = "ESPN-ONESITE.WEB-PROD"
ONEID_BASE = f"https://registerdisney.go.com/jgc/v8/client/{CLIENT_ID}"

#: ESPN's web client sends a fixed public API key on every OneID call as
#: `authorization: APIKEY <key>`. It is embedded in espn.com's JavaScript and is
#: not a user secret, but it does change, so it is configuration rather than a
#: constant. The discovery script reads it from the Network tab; the service
#: reads it from `FWR_ESPN_ONEID_API_KEY`. Without it, OneID answers 401.
API_KEY_ENV = "FWR_ESPN_ONEID_API_KEY"

DEFAULT_TIMEOUT = 15.0

#: Query string every OneID call in this flow carries.
_FLOW_QUERY = {"langPref": "en-US", "feature": "no-password-reuse"}


def api_key_from_env() -> str:
    return (os.environ.get(API_KEY_ENV) or "").strip()


class OneIDError(RuntimeError):
    """A OneID step failed. The message is already redacted."""

    def __init__(self, message: str, step: str = "", status_code: int | None = None) -> None:
        super().__init__(redact(message))
        self.step = step
        self.status_code = status_code


@dataclass
class OneIDResult:
    """The outcome of one OneID call, minus anything sensitive.

    `raw` is kept in memory only for the discovery script to inspect; it is
    never logged and never persisted. Production code reads the typed fields.
    """

    ok: bool
    status_code: int
    #: Set-Cookie seen on this response, name -> value. Accumulated by the flow.
    cookies: dict[str, str] = field(default_factory=dict)
    #: In-memory only. The discovery script redacts before display.
    raw: Any = None


def _deep_find(node: Any, keys: set[str], depth: int = 0) -> Any:
    """First value under any of `keys`, searched case-insensitively, any depth.

    Disney has reshaped this payload before and the exact path is unverified, so
    a search is more robust than a fixed path -- and it degrades to "not found"
    rather than a crash when the shape moves.
    """
    if depth > 8 or node is None:
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.lower() in keys and isinstance(v, (str, int)):
                return v
        for v in node.values():
            found = _deep_find(v, keys, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _deep_find(v, keys, depth + 1)
            if found is not None:
                return found
    return None


def _normalise_swid(value: Any) -> str:
    value = str(value or "").strip().strip('"')
    if not value:
        return ""
    if not value.startswith("{"):
        value = "{" + value
    if not value.endswith("}"):
        value = value + "}"
    return value.upper()


def looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", (value or "").strip()))


class DisneyOneID:
    """One passwordless OneID recovery flow, one step at a time.

    Each public method is exactly one transition and one HTTP call, so a break
    is attributable to a single step rather than lost inside a monolith. State
    that a later step needs is carried on the instance; none of it is sensitive
    enough to persist, and the instance is discarded when the flow ends.
    """

    def __init__(
        self,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise OneIDError(
                "No OneID API key configured. Set FWR_ESPN_ONEID_API_KEY "
                "(the `authorization: APIKEY ...` value from espn.com).",
                step="config",
            )
        self._api_key = api_key
        self._conversation_id = str(uuid.uuid4())
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,  # a redirect here usually means auth failed
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"APIKEY {api_key}",
                "conversation-id": self._conversation_id,
                "oneid-client-id": CLIENT_ID,
                "User-Agent": "FantasyWarRoom/1.0",
            },
        )
        #: Cross-request pieces the flow itself must echo. Never persisted.
        self._flow_token: str | None = None
        self._otp_session: str | None = None
        self._disney_token: str | None = None
        #: Every ESPN cookie seen across the whole flow, name -> value.
        self._cookie_jar: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DisneyOneID":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- plumbing ----------------------------------------------------------

    def _call(self, method: str, path: str, step: str, body: dict | None = None) -> OneIDResult:
        url = ONEID_BASE + path
        # APIKEY (app auth) stays on every call via the client headers. The
        # in-progress flow token is carried in the body where the contract
        # expects it, not as an Authorization override -- APIKEY must remain.
        headers = {"correlation-id": str(uuid.uuid4())}
        try:
            response = self._client.request(
                method, url, params=_FLOW_QUERY, json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise OneIDError(f"Could not reach Disney OneID: {exc}", step=step) from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None

        cookies = dict(response.cookies)
        self._cookie_jar.update(cookies)

        if response.status_code >= 400:
            # CONTRACT: OneID error bodies carry a code under `error` / `errors`.
            detail = _deep_find(payload, {"message", "code", "error"}) or ""
            raise OneIDError(
                f"OneID {step} failed ({response.status_code}): {detail}",
                step=step,
                status_code=response.status_code,
            )

        return OneIDResult(
            ok=True,
            status_code=response.status_code,
            cookies=cookies,
            raw=payload if payload is not None else response.text,
        )

    # -- step 1: CREATED ---------------------------------------------------

    def start_flow(self) -> OneIDResult:
        """Establish a recovery flow context.

        CONTRACT: `/guest-flow` is expected to return a flow / conversation
        token that the OTP request then references.
        """
        result = self._call("POST", "/guest-flow", step="start_flow", body={})
        self._flow_token = _deep_find(
            result.raw, {"flowtoken", "flow_token", "token", "conversationtoken"}
        )
        return result

    # -- step 2: OTP_SENT --------------------------------------------------

    def request_otp(self, email: str) -> OneIDResult:
        """Ask Disney to email a one-time code to `email`.

        CONTRACT: `/notification/otp/recovery` with the address as the login
        value. `flowToken` echoes the context from step 1 when present.
        """
        body: dict[str, Any] = {"loginValue": email, "intent": ""}
        if self._flow_token:
            body["flowToken"] = self._flow_token
        result = self._call(
            "POST", "/notification/otp/recovery", step="request_otp", body=body
        )
        self._otp_session = _deep_find(
            result.raw, {"otpsession", "otp_session", "session", "flowtoken"}
        )
        return result

    # -- step 3: OTP_VERIFIED ---------------------------------------------

    def submit_otp(self, code: str) -> OneIDResult:
        """Redeem the six-digit code for a Disney session.

        CONTRACT: `/otp/redeem` returns the token object
        (`data.token.access_token`, `refresh_token`, ...). We keep only the
        access token, and only long enough to establish the ESPN session.
        """
        body: dict[str, Any] = {"passcode": (code or "").strip()}
        if self._otp_session:
            body["otpSession"] = self._otp_session
        result = self._call("POST", "/otp/redeem", step="submit_otp", body=body)
        self._disney_token = _deep_find(
            result.raw, {"access_token", "accesstoken", "id_token", "token"}
        )
        # SWID is the OneID GUID and is frequently a claim on the token payload.
        swid = _deep_find(result.raw, {"swid"})
        if swid:
            self._cookie_jar.setdefault("SWID", _normalise_swid(swid))
        return result

    # -- step 4: ESPN_SESSION_ESTABLISHED ---------------------------------

    def establish_espn_session(self) -> tuple[str, str]:
        """Turn the Disney session into ESPN's `SWID` + `espn_s2`.

        Sources tried, most-authoritative first:

        1. Any `Set-Cookie` accumulated across the flow -- redeeming the OTP
           often sets the ESPN cookies directly.
        2. The token payload -- `SWID` is the OneID GUID, so it is frequently
           present even when only `espn_s2` needs a cookie.
        3. One more GET, carrying the Disney session, to mint `espn_s2`.

        Returns `(swid, espn_s2)`. Raises if either is missing, because a
        session without both is unusable and must not be stored as if it were.
        """
        swid = _normalise_swid(self._cookie_jar.get("SWID"))
        espn_s2 = self._cookie_jar.get("espn_s2") or self._cookie_jar.get("ESPN_S2") or ""

        if not swid or not espn_s2:
            # CONTRACT: the least-certain step. Some flows need one more GET to
            # an ESPN endpoint carrying the Disney token to mint espn_s2. The
            # discovery script pins the real endpoint and its response shape.
            try:
                result = self._call("GET", "/guest/tokens", step="establish")
                if not swid:
                    swid = _normalise_swid(
                        self._cookie_jar.get("SWID") or _deep_find(result.raw, {"swid"})
                    )
                if not espn_s2:
                    espn_s2 = (
                        self._cookie_jar.get("espn_s2")
                        or _deep_find(result.raw, {"espn_s2"})
                        or ""
                    )
            except OneIDError:
                pass  # fall through to the explicit failure below

        if not swid or not espn_s2:
            missing = ", ".join(
                name for name, present in (("SWID", swid), ("espn_s2", espn_s2)) if not present
            )
            raise OneIDError(
                f"Authenticated, but could not obtain {missing} from the ESPN session. "
                "The redeem step's response shape needs verifying against live Disney.",
                step="establish",
            )
        return swid, str(espn_s2)
