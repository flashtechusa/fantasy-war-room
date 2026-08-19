"""The ESPN Email Code (OTP) connection flow.

The Disney endpoints are unreachable from CI and their contract is unverified,
so these drive a mock Disney via an injected transport. What they pin is the
part that is ours and must not regress: the state machine, the ten-minute
expiry, the user binding, the reduction to exactly SWID+espn_s2, and that no
credential or email ever escapes.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.espn import oneid
from app.services import espn_otp

from test_espn_discovery import MY_SWID, league_payload

EMAIL = "stephen@example.com"
S2 = "AEB" + "x9Kq2Lm" * 30


# ---------------------------------------------------------------------------
# A mock Disney that answers the four OTP endpoints.
# ---------------------------------------------------------------------------


class FakeDisney:
    """Stand-in for registerdisney, matching the observed HAR contract.

    The four real endpoints and their real request/response keys:
      1 /guest/recovery-methods     {loginValue}   -> data.recoveryMethods[]
      2 /notification/otp/recovery  {lookupValue}  -> data.sessionId
      3 /otp/redeem                 {passcode,     -> data.swid,
                                      sessionIds}      data.recoveryToken.access_token
      4 /guest/login/recoveryToken  {swid,         -> data.s2, data.profile.swid
                                      recoveryToken}
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.otp_ok = True
        self.has_account = True
        #: What the final login exchange returns.
        self.login_s2 = S2
        self.profile_swid = MY_SWID
        #: What redeem returns; step 4 validates these agree with the profile.
        self.redeemed_swid = MY_SWID

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        self.bodies.append(body)
        path = request.url.path

        if path.endswith("/guest/recovery-methods"):
            methods = (
                [{"id": "e1", "type": "EMAIL", "mask": "s***@e***.com"}]
                if self.has_account
                else []
            )
            return httpx.Response(200, json={"data": {"recoveryMethods": methods}})

        if path.endswith("/notification/otp/recovery"):
            return httpx.Response(
                200,
                json={"data": {"sessionId": "sess-1", "broadcastId": "bc-1",
                               "expirationTime": 600}},
            )

        if path.endswith("/otp/redeem"):
            if not self.otp_ok:
                return httpx.Response(401, json={"error": {"code": "INVALID_OTP"}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "access_token": "disney-access-token-value",
                        "swid": self.redeemed_swid,
                        "recoveryToken": {
                            "access_token": "recovery-token-value",
                            "swid": self.redeemed_swid,
                            "identity_id": "identity-123",
                        },
                    }
                },
            )

        if path.endswith("/guest/login/recoveryToken"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "s2": self.login_s2,
                        "profile": {"swid": self.profile_swid},
                        "token": {"access_token": "final-access-token"},
                    }
                },
            )
        return httpx.Response(404, json={})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture(autouse=True)
def _otp_enabled(monkeypatch):
    monkeypatch.setenv("FWR_ESPN_OTP_ENABLED", "1")
    # Every test starts with an empty flow registry.
    espn_otp.registry._flows.clear()
    yield
    espn_otp.registry._flows.clear()


@pytest.fixture
def disney(monkeypatch):
    """Route every DisneyOneID through a FakeDisney matching the real contract."""
    fake = FakeDisney()
    real_init = oneid.DisneyOneID.__init__

    def patched_init(self, timeout=15.0, transport=None):
        real_init(self, timeout=timeout, transport=fake.transport)

    monkeypatch.setattr(oneid.DisneyOneID, "__init__", patched_init)
    return fake


# ---------------------------------------------------------------------------
# The OneID client in isolation
# ---------------------------------------------------------------------------


class TestDisneyOneID:
    @staticmethod
    def run_flow(fake) -> tuple[str, str]:
        client = oneid.DisneyOneID()
        client.recovery_methods(EMAIL)
        client.request_otp(EMAIL)
        client.submit_otp("123456")
        return client.establish_espn_session()

    def test_the_four_steps_reduce_to_the_two_cookies(self, disney):
        swid, espn_s2 = self.run_flow(disney)
        assert swid == MY_SWID.upper()
        assert espn_s2 == S2

    def test_no_authorization_header_is_sent(self, disney):
        """The real capture carried no APIKEY / bearer -- neither do we."""
        self.run_flow(disney)
        for request in disney.requests:
            assert "Authorization" not in request.headers

    def test_the_steps_use_the_observed_request_keys(self, disney):
        self.run_flow(disney)
        by_path = {r.url.path.split("/")[-1]: b for r, b in zip(disney.requests, disney.bodies)}
        assert "loginValue" in by_path["recovery-methods"]
        assert "lookupValue" in by_path["recovery"]
        redeem = by_path["redeem"]
        assert "passcode" in redeem and isinstance(redeem["sessionIds"], list)
        login = by_path["recoveryToken"]
        assert "swid" in login and "recoveryToken" in login

    def test_the_session_id_is_echoed_into_redeem(self, disney):
        self.run_flow(disney)
        redeem = next(b for r, b in zip(disney.requests, disney.bodies)
                      if r.url.path.endswith("/otp/redeem"))
        assert redeem["sessionIds"] == ["sess-1"]

    def test_the_email_is_sent_to_disney_but_never_survives_redaction(self, disney):
        client = oneid.DisneyOneID()
        client.recovery_methods(EMAIL)
        assert EMAIL in disney.requests[-1].content.decode()  # goes TO Disney
        assert EMAIL not in oneid.redact(f"failed for {EMAIL}")  # not back out

    def test_an_unknown_email_fails_early_and_clearly(self, disney):
        disney.has_account = False
        client = oneid.DisneyOneID()
        with pytest.raises(oneid.OneIDError, match="no recovery method"):
            client.recovery_methods(EMAIL)

    def test_a_bad_otp_raises_at_the_redeem_step(self, disney):
        disney.otp_ok = False
        client = oneid.DisneyOneID()
        client.recovery_methods(EMAIL)
        client.request_otp(EMAIL)
        with pytest.raises(oneid.OneIDError) as excinfo:
            client.submit_otp("000000")
        assert excinfo.value.step == "submit_otp"

    def test_profile_swid_without_braces_is_wrapped(self, disney):
        disney.profile_swid = "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
        disney.redeemed_swid = "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
        swid, _ = self.run_flow(disney)
        assert swid == "{1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D}"

    def test_a_login_missing_s2_is_rejected(self, disney):
        disney.login_s2 = ""
        client = oneid.DisneyOneID()
        client.recovery_methods(EMAIL)
        client.request_otp(EMAIL)
        client.submit_otp("123456")
        with pytest.raises(oneid.OneIDError, match="espn_s2"):
            client.establish_espn_session()

    def test_a_mismatched_profile_and_redeemed_swid_is_refused(self, disney):
        # The account that logged in is not the one whose code was redeemed.
        disney.profile_swid = "{99999999-8888-7777-6666-555555555555}"
        client = oneid.DisneyOneID()
        client.recovery_methods(EMAIL)
        client.request_otp(EMAIL)
        client.submit_otp("123456")
        with pytest.raises(oneid.OneIDError, match="did not match"):
            client.establish_espn_session()


# ---------------------------------------------------------------------------
# The state machine + API, end to end (proof step stubbed to a mock ESPN)
# ---------------------------------------------------------------------------


@pytest.fixture
def espn_reader(monkeypatch):
    """Point discovery's ESPN client at a mock so the proof step can run."""
    from app.services import espn_connect

    fake_leagues = {30039838: league_payload(30039838, "FWR ESPN Test", my_team=1)}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "fan.api.espn.com" in url:
            # Fan profile lists the test league.
            from test_espn_discovery import fan_entry, fan_profile

            return httpx.Response(200, json=fan_profile(fan_entry(30039838, "FWR ESPN Test")))
        league_id = int(url.split("/leagues/")[1].split("?")[0])
        if league_id in fake_leagues:
            return httpx.Response(200, json=fake_leagues[league_id])
        return httpx.Response(404, json={})

    real = espn_connect.EspnHttpClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(espn_connect, "EspnHttpClient", factory)
    return fake_leagues


def start(client, email: str = EMAIL):
    return client.post("/api/espn/otp/start", json={"email": email})


class TestOtpApi:
    def test_status_reports_otp_availability(self, client):
        body = client.get("/api/espn/status").json()
        assert body["otp_available"] is True  # on by default
        assert body["public_link_available"] is True
        assert body["manual_entry_available"] is True

    def test_the_kill_switch_hides_otp(self, client, monkeypatch):
        monkeypatch.setenv("FWR_ESPN_OTP_ENABLED", "0")
        assert client.get("/api/espn/status").json()["otp_available"] is False

    def test_start_sends_a_code_and_returns_a_flow(self, client, disney):
        body = start(client).json()
        assert body["sent"] is True
        assert body["state"] == "OTP_SENT"
        assert body["expires_in_seconds"] <= 600
        assert "flow_id" in body

    def test_start_rejects_a_non_email(self, client, disney):
        assert start(client, "not-an-email").status_code == 422

    def test_the_full_flow_connects_and_proves_a_private_league(
        self, client, disney, espn_reader
    ):
        flow = start(client).json()
        result = client.post(
            "/api/espn/otp/verify",
            json={"flow_id": flow["flow_id"], "code": "123456"},
        ).json()
        assert result["connected"] is True
        assert result["verified"] is True
        assert result["proof"]["confirmed"] is True
        assert result["proof"]["leagues_found"] == 1
        # Cookies were stored through the shared path.
        assert result["status"]["credentials_stored"] is True

    def test_no_credential_or_email_appears_in_any_response(
        self, client, disney, espn_reader
    ):
        flow = start(client).json()
        verify = client.post(
            "/api/espn/otp/verify",
            json={"flow_id": flow["flow_id"], "code": "123456"},
        )
        for text in (json.dumps(flow), verify.text, client.get("/api/espn/status").text):
            assert MY_SWID not in text
            assert S2 not in text
            assert EMAIL not in text
            assert "recovery-token-value" not in text
            assert "final-access-token" not in text

    def test_a_wrong_code_is_a_400_not_a_500(self, client, disney):
        disney.otp_ok = False
        flow = start(client).json()
        response = client.post(
            "/api/espn/otp/verify",
            json={"flow_id": flow["flow_id"], "code": "000000"},
        )
        assert response.status_code == 400

    def test_a_non_numeric_code_is_refused_before_disney(self, client, disney):
        flow = start(client).json()
        response = client.post(
            "/api/espn/otp/verify",
            json={"flow_id": flow["flow_id"], "code": "abcd"},
        )
        assert response.status_code == 422  # schema rejects it

    def test_an_unknown_flow_id_is_rejected(self, client, disney):
        response = client.post(
            "/api/espn/otp/verify",
            json={"flow_id": "does-not-exist-1234", "code": "123456"},
        )
        assert response.status_code == 400

    def test_a_signed_out_browser_cannot_start_a_flow(self, anon_client):
        assert anon_client.post("/api/espn/otp/start", json={"email": EMAIL}).status_code == 401


class TestFlowRegistry:
    def test_a_flow_is_bound_to_its_user(self):
        from app.models import User

        alice = User(id=1, username="alice")
        bob = User(id=2, username="bob")
        flow = espn_otp.registry.create(alice)
        # Bob cannot fetch Alice's flow even with the id.
        with pytest.raises(espn_otp.OtpFlowError):
            espn_otp.registry.get(flow.flow_id, bob)
        # Alice can.
        assert espn_otp.registry.get(flow.flow_id, alice).flow_id == flow.flow_id

    def test_an_expired_flow_is_gone(self, monkeypatch):
        from app.models import User

        user = User(id=1, username="alice")
        flow = espn_otp.registry.create(user)
        # Force it past its TTL.
        flow.created_at = flow.created_at - espn_otp.FLOW_TTL
        with pytest.raises(espn_otp.OtpFlowError, match="expired"):
            espn_otp.registry.get(flow.flow_id, user)

    def test_ttl_is_ten_minutes(self):
        assert espn_otp.FLOW_TTL.total_seconds() == 600
