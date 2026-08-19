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
the recovery token, the identity id, the session-transfer material, the OTP
itself) is discarded. The rest of the app then uses the existing ESPN
connection layer, which already understands exactly those two cookies. There is
deliberately no second authentication architecture downstream of this.

THE OBSERVED CONTRACT
---------------------
The request/response shapes below are taken from a real browser capture of the
ESPN passwordless login (HAR), not guessed. The four calls, all POST under
`/jgc/v8/client/ESPN-ONESITE.WEB-PROD`:

1. `/guest/recovery-methods`         req {loginValue}      -> data.recoveryMethods[]
2. `/notification/otp/recovery`      req {lookupValue}     -> data.sessionId, expirationTime
3. `/otp/redeem`                     req {passcode,        -> data.swid,
                                          sessionIds[]}       data.recoveryToken.access_token
4. `/guest/login/recoveryToken`      req {swid,            -> data.s2,
      ?expand=...&expand=s2               recoveryToken}      data.profile.swid, data.token

Final credentials:
    SWID     = data.profile.swid   (validated to agree with the redeemed swid)
    espn_s2  = data.s2

The captured requests carried **no** `Authorization: APIKEY` / `X-API-Key`
header, so none is sent here.

SECURITY
--------
* The ESPN password is never collected -- there is no password in an OTP flow.
* The OTP, the recovery/access/identity tokens and the session material never
  touch the database and are never logged.
* Raw Disney responses are never logged. Errors are redacted (`redact()` also
  scrubs email addresses and long tokens) before they leave this module.
* The only thing that persists is `SWID` + `espn_s2`, through the existing
  encrypted store.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from .redaction import redact

log = logging.getLogger(__name__)

#: The OneID client the ESPN *website* identifies as, in the URL path.
CLIENT_ID = "ESPN-ONESITE.WEB-PROD"
ONEID_BASE = f"https://registerdisney.go.com/jgc/v8/client/{CLIENT_ID}"

DEFAULT_TIMEOUT = 15.0

#: Query params, per step, exactly as the browser sends them. Lists of tuples
#: because step 4 repeats `expand`.
_Q_COMMON = [("langPref", "en-US"), ("feature", "no-password-reuse")]
_Q_RECOVERY_METHODS = _Q_COMMON
_Q_OTP_SEND = [("intent", "")] + _Q_COMMON
_Q_OTP_REDEEM = _Q_COMMON
_Q_LOGIN_RECOVERY = [
    ("expand", "profile"),
    ("expand", "displayname"),
    ("expand", "linkedaccounts"),
    ("expand", "marketing"),
    ("expand", "entitlements"),
    ("expand", "s2"),
] + _Q_COMMON


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
    cookies: dict[str, str] = field(default_factory=dict)
    raw: Any = None


def _data(payload: Any) -> dict:
    """Unwrap the `data` envelope OneID wraps every response in."""
    if isinstance(payload, dict):
        inner = payload.get("data")
        return inner if isinstance(inner, dict) else payload
    return {}


def _get(node: Any, *path: str) -> Any:
    """Walk a fixed key path; return None if any hop is missing."""
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _deep_find(node: Any, keys: set[str], depth: int = 0) -> Any:
    """Fallback search when the fixed path misses (one HAR is one sample)."""
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
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,  # a redirect here usually means auth failed
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "conversation-id": str(uuid.uuid4()),
                "User-Agent": "FantasyWarRoom/1.0",
            },
        )
        #: Cross-request pieces the flow itself must echo. Never persisted.
        self._session_id: str | None = None
        self._redeemed_swid: str | None = None
        self._recovery_token: str | None = None
        #: Any ESPN cookie seen via Set-Cookie across the flow (belt-and-braces).
        self._cookie_jar: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DisneyOneID":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- plumbing ----------------------------------------------------------

    def _post(self, path: str, params, step: str, body: dict) -> OneIDResult:
        url = ONEID_BASE + path
        headers = {"correlation-id": str(uuid.uuid4())}
        try:
            response = self._client.post(url, params=params, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise OneIDError(f"Could not reach Disney OneID: {exc}", step=step) from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None

        cookies = dict(response.cookies)
        self._cookie_jar.update(cookies)

        if response.status_code >= 400:
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

    # -- step 1: CREATED (confirm the account, seed the flow) --------------

    def recovery_methods(self, email: str) -> OneIDResult:
        """`/guest/recovery-methods` -- which recovery channels the account has.

        The browser makes this call before sending a code; it also fails early
        and clearly when the email is not a known ESPN account.
        """
        result = self._post(
            "/guest/recovery-methods",
            _Q_RECOVERY_METHODS,
            step="recovery_methods",
            body={"loginValue": email},
        )
        methods = _get(result.raw, "data", "recoveryMethods")
        if isinstance(methods, list) and not methods:
            raise OneIDError(
                "ESPN has no recovery method for that email. Check the address.",
                step="recovery_methods",
            )
        return result

    # -- step 2: OTP_SENT --------------------------------------------------

    def request_otp(self, email: str) -> OneIDResult:
        """`/notification/otp/recovery` -- email the six-digit code.

        The request key is `lookupValue`; the response carries the `sessionId`
        that the redeem step must echo back.
        """
        result = self._post(
            "/notification/otp/recovery",
            _Q_OTP_SEND,
            step="request_otp",
            body={"lookupValue": email},
        )
        self._session_id = _get(result.raw, "data", "sessionId") or _deep_find(
            result.raw, {"sessionid"}
        )
        if not self._session_id:
            raise OneIDError(
                "ESPN did not return a session for the code request.",
                step="request_otp",
            )
        return result

    # -- step 3: OTP_VERIFIED ---------------------------------------------

    def submit_otp(self, code: str) -> OneIDResult:
        """`/otp/redeem` -- redeem the code for a recovery token.

        The response carries `data.swid` and `data.recoveryToken.access_token`;
        both feed the final login exchange.
        """
        result = self._post(
            "/otp/redeem",
            _Q_OTP_REDEEM,
            step="submit_otp",
            body={"passcode": (code or "").strip(), "sessionIds": [self._session_id]},
        )
        data = _data(result.raw)
        self._redeemed_swid = _normalise_swid(
            _get(data, "swid") or _get(data, "recoveryToken", "swid")
        )
        self._recovery_token = (
            _get(data, "recoveryToken", "access_token")
            or _deep_find(_get(data, "recoveryToken"), {"access_token", "accesstoken"})
        )
        if not self._recovery_token:
            raise OneIDError(
                "ESPN accepted the code but returned no recovery token.",
                step="submit_otp",
            )
        return result

    # -- step 4: ESPN_SESSION_ESTABLISHED ---------------------------------

    def establish_espn_session(self) -> tuple[str, str]:
        """`/guest/login/recoveryToken` -- exchange the recovery token for `s2`.

        Requesting `expand=s2` makes the response carry `data.s2` (the espn_s2
        value) and `data.profile.swid` directly. The profile SWID is validated
        against the SWID redeemed in step 3 before either is trusted -- they
        must be the same account.

        Returns `(swid, espn_s2)`. Raises if either is missing, because a
        session without both is unusable and must not be stored as if it were.
        """
        if not self._recovery_token:
            raise OneIDError("No recovery token; redeem a code first.", step="establish")

        result = self._post(
            "/guest/login/recoveryToken",
            _Q_LOGIN_RECOVERY,
            step="establish",
            body={"swid": self._redeemed_swid, "recoveryToken": self._recovery_token},
        )
        data = _data(result.raw)

        espn_s2 = (
            _get(data, "s2")
            or self._cookie_jar.get("espn_s2")
            or _deep_find(data, {"s2", "espn_s2"})
            or ""
        )
        profile_swid = _normalise_swid(
            _get(data, "profile", "swid")
            or self._cookie_jar.get("SWID")
            or _deep_find(data, {"swid"})
        )

        if not profile_swid or not espn_s2:
            missing = ", ".join(
                name for name, present in (("SWID", profile_swid), ("espn_s2", espn_s2))
                if not present
            )
            raise OneIDError(
                f"Logged in, but the response did not carry {missing}.",
                step="establish",
            )

        # Both the redeemed SWID (step 3) and the profile SWID (step 4) describe
        # the account we just authenticated. If they disagree, something is
        # wrong -- refuse rather than store a mismatched identity.
        if self._redeemed_swid and profile_swid != self._redeemed_swid:
            raise OneIDError(
                "The logged-in account did not match the code that was redeemed.",
                step="establish",
            )

        return profile_swid, str(espn_s2)
