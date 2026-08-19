"""The ESPN Email Code (OTP) connection flow -- a four-state machine.

This is the *primary* connect method: the user enters their ESPN email, Disney
emails a code, and redeeming it establishes an authenticated ESPN session. It
works for public and private leagues alike, and because it authenticates, it
verifies ESPN identity and team ownership -- which the public-link path cannot.

WHERE IT SITS IN THE ARCHITECTURE
---------------------------------
OTP is *only* a way to acquire `SWID` + `espn_s2`. The instant those two
cookies exist, this module hands them to the same encrypted store every other
method uses (`espn_connect.save_credentials`) and steps out of the way.
Discovery, team detection, import, live-draft sync and disconnect all run on the
one existing ESPN connection layer afterward. There is deliberately no second
downstream implementation for OTP.

STATE MACHINE
-------------
    CREATED  ──start──▶  OTP_SENT  ──verify──▶  OTP_VERIFIED
                                                     │
                                                 (reduce to
                                                  SWID+espn_s2,
                                                  store, prove
                                                  against a
                                                  private league)
                                                     ▼
                                          ESPN_SESSION_ESTABLISHED

Every transition is explicit and separately attributable, so when Disney
changes a step -- and it will -- diagnostics point at exactly which one broke.

WHAT NEVER PERSISTS
-------------------
The whole flow lives in memory, keyed by a flow id, bound to the signed-in
War Room user, and expiring in ten minutes. The OTP, the Disney tokens and the
recovery context never touch the database and are never logged. Only the final
`SWID` + `espn_s2` are persisted, through the existing encrypted path.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

import httpx

from ..models import User
from ..espn.oneid import DisneyOneID, OneIDError, looks_like_email
from ..espn.redaction import redact

__all__ = [
    "OtpState",
    "OtpFlow",
    "OtpFlowError",
    "registry",
    "start_flow",
    "verify_code",
    "otp_enabled",
    "FLOW_TTL",
]

log = logging.getLogger(__name__)

#: An unfinished flow is useless after this and holds a live Disney handle, so
#: it is dropped promptly rather than lingering.
FLOW_TTL = timedelta(minutes=10)


def otp_enabled() -> bool:
    """Whether the ESPN Email Code method is offered.

    On by default -- it needs no configuration now that the OneID contract
    carries no API key. `FWR_ESPN_OTP_ENABLED=0` is a kill switch for an
    experimental feature with an external dependency, so it can be turned off
    without a redeploy if Disney's flow misbehaves.
    """
    return os.environ.get("FWR_ESPN_OTP_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


class OtpState(str, Enum):
    CREATED = "CREATED"
    OTP_SENT = "OTP_SENT"
    OTP_VERIFIED = "OTP_VERIFIED"
    ESPN_SESSION_ESTABLISHED = "ESPN_SESSION_ESTABLISHED"
    FAILED = "FAILED"


class OtpFlowError(RuntimeError):
    """A flow step could not be completed. Message is already redacted."""

    def __init__(self, message: str, step: str = "") -> None:
        super().__init__(redact(message))
        self.step = step


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class OtpFlow:
    """One in-flight OTP flow. In memory only; nothing here is persisted."""

    flow_id: str
    user_id: int
    state: OtpState = OtpState.CREATED
    created_at: datetime = field(default_factory=_now)
    #: The live Disney handle carrying the recovery context between steps.
    client: DisneyOneID | None = None
    #: Redacted, user-facing note about the last step. Never a credential.
    last_error: str = ""

    @property
    def expires_at(self) -> datetime:
        return self.created_at + FLOW_TTL

    @property
    def expired(self) -> bool:
        return _now() >= self.expires_at

    def public_state(self) -> dict:
        return {
            "flow_id": self.flow_id,
            "state": self.state.value,
            "expires_in_seconds": max(0, int((self.expires_at - _now()).total_seconds())),
            "last_error": self.last_error,
        }


class OtpFlowRegistry:
    """Process-wide store of in-flight flows. Thread-safe, self-pruning."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flows: dict[str, OtpFlow] = {}

    def _prune(self) -> None:
        for flow_id in [fid for fid, f in self._flows.items() if f.expired]:
            self._drop_locked(flow_id)

    def _drop_locked(self, flow_id: str) -> None:
        flow = self._flows.pop(flow_id, None)
        if flow and flow.client is not None:
            try:
                flow.client.close()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    def create(self, user: User) -> OtpFlow:
        flow = OtpFlow(flow_id=secrets.token_urlsafe(18), user_id=user.id)
        with self._lock:
            self._prune()
            self._flows[flow.flow_id] = flow
        return flow

    def get(self, flow_id: str, user: User) -> OtpFlow:
        """Fetch a flow, enforcing that it belongs to this user and is live.

        Binding to the user id is what stops one account redeeming another's
        flow -- a flow id alone is never enough.
        """
        with self._lock:
            self._prune()
            flow = self._flows.get(flow_id or "")
            if flow is None or flow.user_id != user.id:
                raise OtpFlowError(
                    "That code request has expired or is not yours. Start again.",
                    step="lookup",
                )
            if flow.expired:
                self._drop_locked(flow_id)
                raise OtpFlowError("That code request has expired. Start again.", step="lookup")
            return flow

    def drop(self, flow_id: str) -> None:
        with self._lock:
            self._drop_locked(flow_id)


#: Process-wide instance. OTP flows are a single-process concern.
registry = OtpFlowRegistry()


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def start_flow(
    user: User, email: str, transport: httpx.BaseTransport | None = None
) -> OtpFlow:
    """CREATED -> OTP_SENT. Establish the Disney flow and send the code.

    `transport` is injectable so tests drive a mock Disney; production leaves it
    None and hits the real endpoints.
    """
    if not looks_like_email(email):
        raise OtpFlowError("That does not look like an email address.", step="start")

    flow = registry.create(user)
    client = DisneyOneID(transport=transport)
    flow.client = client
    try:
        # Step 1 confirms the account exists and seeds the flow; step 2 sends
        # the code and returns the session id the redeem step needs.
        client.recovery_methods(email)
        client.request_otp(email)
    except OneIDError as exc:
        flow.state = OtpState.FAILED
        flow.last_error = str(exc)
        # Do not leak which step failed to a caller beyond the redacted message;
        # the step tag is for diagnostics, carried on the exception.
        raise OtpFlowError(str(exc), step=exc.step) from exc

    flow.state = OtpState.OTP_SENT
    return flow


def verify_code(
    session,
    user: User,
    flow_id: str,
    code: str,
) -> dict:
    """OTP_SENT -> ESPN_SESSION_ESTABLISHED.

    Redeems the code, reduces the result to `SWID` + `espn_s2`, stores them
    through the shared encrypted path, and *proves* the session by reading a
    private league -- not merely an account/profile endpoint. Returns the same
    connection status shape every other method produces.
    """
    from . import espn_connect

    flow = registry.get(flow_id, user)
    if flow.state not in (OtpState.OTP_SENT, OtpState.OTP_VERIFIED):
        raise OtpFlowError(
            f"This flow is {flow.state.value}, not awaiting a code. Start again.",
            step="verify",
        )
    if flow.client is None:  # pragma: no cover - only if a flow was mishandled
        raise OtpFlowError("This flow has no active session. Start again.", step="verify")

    code = (code or "").strip()
    if not code.isdigit() or not 4 <= len(code) <= 8:
        raise OtpFlowError("Enter the numeric code from your email.", step="verify")

    try:
        flow.client.submit_otp(code)
        flow.state = OtpState.OTP_VERIFIED
        swid, espn_s2 = flow.client.establish_espn_session()
    except OneIDError as exc:
        flow.last_error = str(exc)
        raise OtpFlowError(str(exc), step=exc.step or "verify") from exc

    # Reduce to the two cookies and hand them to the shared store. Everything
    # else the flow touched is dropped when the flow is dropped, below.
    espn_connect.save_credentials(session, user, swid=swid, espn_s2=espn_s2)
    flow.state = OtpState.ESPN_SESSION_ESTABLISHED

    # Prove authentication against a real private league, not a profile ping.
    proof = _prove_private_access(session, user)

    # The flow has done its job; drop it so the Disney handle and context die.
    registry.drop(flow_id)

    from . import board as board_service

    board_service.clear_cache()

    return {
        "connected": True,
        "verified": True,
        "proof": proof,
        "status": espn_connect.status(session, user),
    }


def _prove_private_access(session, user) -> dict:
    """Confirm the stored cookies actually read private data.

    Runs the existing discovery, which reads each league's teams via an
    authenticated `mSettings`+`mTeam` call -- a private, account-gated read. One
    league coming back with teams is proof the session works; zero is a warning,
    not a hard failure, because a brand-new account may genuinely have no teams.
    """
    from . import espn_connect

    try:
        result = espn_connect.discover(session, user)
    except espn_connect.EspnConnectError as exc:
        return {"leagues_found": 0, "detail": redact(exc), "confirmed": False}

    return {
        "leagues_found": len(result.leagues),
        "confirmed": len(result.leagues) > 0,
        "detail": (
            f"Read {len(result.leagues)} league(s) with authenticated access."
            if result.leagues
            else "Session established, but this account has no leagues this season."
        ),
    }
