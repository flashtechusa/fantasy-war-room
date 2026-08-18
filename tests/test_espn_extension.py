"""The browser extension's contract with the backend.

Two things are tested here:

* the **pairing code**, which is the extension's only credential for this app.
  It is issued to a signed-in user, single-use, short-lived, and revoked on
  disconnect -- so each of those properties gets a test.
* the **payload shape**, checked against the extension's own source, so the two
  halves cannot drift apart silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.api import routes_espn
from app.services import espn_connect

from test_espn_discovery import MY_SWID

S2 = "AEB" + "x9Kq2Lm" * 30
EXTENSION_DIR = Path(__file__).resolve().parents[1] / "browser-extension"


@pytest.fixture(autouse=True)
def _clear_throttle():
    routes_espn.reset_throttle()
    yield
    routes_espn.reset_throttle()


def extension_payload(code: str, **overrides) -> dict:
    payload = {
        "pairing_code": code,
        "swid": MY_SWID,
        "espn_s2": S2,
        "league_id": 654321,
        "season": 2026,
        "client": "extension 0.1.0",
    }
    payload.update(overrides)
    return payload


class TestPairingCodes:
    def test_a_code_is_issued_to_a_signed_in_user(self, client):
        body = client.post("/api/espn/pairing-code").json()
        assert len(body["code"]) == espn_connect.PAIRING_CODE_LENGTH
        assert body["expires_in_seconds"] > 0

    def test_codes_avoid_ambiguous_characters(self, client):
        """A human types this off a screen. `0`/`O` and `1`/`I` are a trap."""
        code = client.post("/api/espn/pairing-code").json()["code"]
        assert not set(code) & set("O0I1")

    def test_only_the_hash_is_stored(self, client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        from app.db import session_scope
        from app.models import EspnPairingCode

        with session_scope() as session:
            row = session.query(EspnPairingCode).one()
            assert row.code_hash != code
            assert code not in row.code_hash
            assert len(row.code_hash) == 64

    def test_issuing_a_new_code_revokes_the_old_one(self, client):
        first = client.post("/api/espn/pairing-code").json()["code"]
        client.post("/api/espn/pairing-code")
        response = client.post(
            "/api/espn/extension/connect", json=extension_payload(first)
        )
        assert response.status_code == 401

    def test_a_signed_out_browser_cannot_mint_one(self, anon_client):
        assert anon_client.post("/api/espn/pairing-code").status_code == 401


class TestExtensionConnect:
    def test_a_valid_code_stores_the_credentials(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        # Posted by something with no session cookie -- like the extension.
        response = anon_client.post(
            "/api/espn/extension/connect", json=extension_payload(code)
        )
        assert response.status_code == 201
        assert response.json()["stored"] is True
        assert client.get("/api/espn/status").json()["credentials_stored"] is True

    def test_the_response_carries_no_credential_and_no_account_detail(
        self, client, anon_client
    ):
        code = client.post("/api/espn/pairing-code").json()["code"]
        body = anon_client.post(
            "/api/espn/extension/connect", json=extension_payload(code)
        ).text
        assert MY_SWID not in body
        assert S2 not in body
        assert "tester" not in body

    def test_the_league_id_comes_back_only_as_a_hint(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        body = anon_client.post(
            "/api/espn/extension/connect", json=extension_payload(code)
        ).json()
        assert body["league_hint"] == {"league_id": 654321, "season": 2026}
        # A hint is not a selection: nothing is connected until it is confirmed.
        assert client.get("/api/espn/status").json()["connected"] is False

    def test_a_code_works_exactly_once(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        assert (
            anon_client.post(
                "/api/espn/extension/connect", json=extension_payload(code)
            ).status_code
            == 201
        )
        assert (
            anon_client.post(
                "/api/espn/extension/connect", json=extension_payload(code)
            ).status_code
            == 401
        )

    def test_an_expired_code_is_refused(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        from datetime import timedelta

        from app.db import session_scope
        from app.models import EspnPairingCode, utcnow

        with session_scope() as session:
            row = session.query(EspnPairingCode).one()
            row.expires_at = utcnow() - timedelta(minutes=1)

        assert (
            anon_client.post(
                "/api/espn/extension/connect", json=extension_payload(code)
            ).status_code
            == 401
        )

    def test_a_wrong_code_is_refused(self, anon_client):
        assert (
            anon_client.post(
                "/api/espn/extension/connect", json=extension_payload("WRONGCOD")
            ).status_code
            == 401
        )

    def test_expired_and_wrong_are_indistinguishable(self, client, anon_client):
        """Different messages would tell a guesser when they had guessed one."""
        code = client.post("/api/espn/pairing-code").json()["code"]
        anon_client.post("/api/espn/extension/connect", json=extension_payload(code))
        used = anon_client.post(
            "/api/espn/extension/connect", json=extension_payload(code)
        ).json()["detail"]
        wrong = anon_client.post(
            "/api/espn/extension/connect", json=extension_payload("ZZZZZZZZ")
        ).json()["detail"]
        assert used == wrong

    def test_disconnecting_revokes_an_outstanding_code(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        client.delete("/api/espn")
        assert (
            anon_client.post(
                "/api/espn/extension/connect", json=extension_payload(code)
            ).status_code
            == 401
        )

    def test_repeated_bad_codes_are_throttled(self, anon_client):
        statuses = [
            anon_client.post(
                "/api/espn/extension/connect", json=extension_payload("BADCODE1")
            ).status_code
            for _ in range(12)
        ]
        assert 429 in statuses

    def test_the_extension_endpoint_is_the_only_public_one_on_the_router(
        self, anon_client
    ):
        assert anon_client.get("/api/espn/status").status_code == 401
        assert anon_client.get("/api/espn/leagues").status_code == 401
        assert anon_client.post("/api/espn/select", json={}).status_code == 401
        assert anon_client.get("/api/espn/extension/manifest-contract").status_code == 200


class TestPayloadValidation:
    def test_required_fields_are_enforced(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        for missing in ("pairing_code", "swid", "espn_s2"):
            payload = extension_payload(code)
            payload.pop(missing)
            assert (
                anon_client.post("/api/espn/extension/connect", json=payload).status_code
                == 422
            ), missing

    def test_unknown_fields_are_rejected(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        payload = extension_payload(code, exfiltrate="everything")
        assert (
            anon_client.post("/api/espn/extension/connect", json=payload).status_code == 422
        )

    def test_optional_fields_really_are_optional(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        payload = {"pairing_code": code, "swid": MY_SWID, "espn_s2": S2}
        assert (
            anon_client.post("/api/espn/extension/connect", json=payload).status_code == 201
        )

    def test_an_absurd_league_id_is_rejected(self, client, anon_client):
        code = client.post("/api/espn/pairing-code").json()["code"]
        payload = extension_payload(code, league_id=-1)
        assert (
            anon_client.post("/api/espn/extension/connect", json=payload).status_code == 422
        )

    def test_the_published_contract_matches_the_model(self, anon_client):
        contract = anon_client.get("/api/espn/extension/manifest-contract").json()
        model_fields = set(routes_espn.ExtensionPayload.model_fields)
        assert set(contract["required"]) <= model_fields
        assert set(contract["optional"]) <= model_fields
        assert set(contract["required"]) | set(contract["optional"]) == model_fields
        assert contract["forbidden_extra_fields"] is True


class TestExtensionSource:
    """The shipped extension has to agree with the contract above."""

    @pytest.fixture
    def manifest(self) -> dict:
        return json.loads((EXTENSION_DIR / "manifest.json").read_text())

    @pytest.fixture
    def popup_js(self) -> str:
        return (EXTENSION_DIR / "popup.js").read_text()

    @pytest.fixture
    def popup_code(self, popup_js) -> str:
        """`popup.js` with comments removed.

        The comments describe what the code deliberately does *not* do
        ("never `localStorage`"), so a naive substring check would fail on the
        very sentence promising the behaviour.

        Block comments are only recognised at the start of a line: the host
        permission pattern is built with a template literal ending in `/*`,
        which an unanchored matcher would happily read as a comment opener and
        swallow the rest of the file.
        """
        without_block = re.sub(r"^[ \t]*/\*.*?\*/", "", popup_js, flags=re.S | re.M)
        return re.sub(r"^\s*//.*$", "", without_block, flags=re.M)

    def test_it_is_manifest_v3(self, manifest):
        assert manifest["manifest_version"] == 3

    def test_permissions_are_minimal(self, manifest):
        assert set(manifest["permissions"]) == {"cookies", "activeTab", "storage"}
        # One host, not a wildcard: `cookies` is only as narrow as its hosts.
        assert manifest["host_permissions"] == ["https://fantasy.espn.com/*"]

    def test_no_broad_browsing_permission_is_requested_up_front(self, manifest):
        forbidden = {"<all_urls>", "*://*/*", "http://*/*", "tabs", "webRequest", "history"}
        granted = set(manifest["permissions"]) | set(manifest["host_permissions"])
        assert not granted & forbidden

    def test_there_is_no_content_script(self, manifest):
        assert "content_scripts" not in manifest

    def test_it_posts_to_the_documented_endpoint(self, popup_js):
        assert "/api/espn/extension/connect" in popup_js

    def test_it_sends_exactly_the_fields_the_backend_accepts(self, popup_code):
        # The body literal in popup.js, plus the two conditional additions.
        sent = set(re.findall(r"body\.(\w+)\s*=", popup_code))
        sent |= {"pairing_code", "swid", "espn_s2", "client"}
        assert sent == set(routes_espn.ExtensionPayload.model_fields)

    def test_it_never_writes_a_cookie_anywhere_persistent(self, popup_code):
        assert "localStorage" not in popup_code
        assert "sessionStorage" not in popup_code
        # The only thing stored is the server address.
        stored = re.findall(r"chrome\.storage\.local\.set\((.*?)\)", popup_code, re.S)
        assert stored and all("swid" not in block.lower() for block in stored)

    def test_it_never_logs(self, popup_code):
        assert "console.log" not in popup_code
        assert "console.warn" not in popup_code
        assert "console.error" not in popup_code

    def test_it_refuses_plain_http_off_loopback(self, popup_js):
        assert "isUsableServer" in popup_js
        assert "localhost" in popup_js and "127.0.0.1" in popup_js

    def test_it_does_not_send_ambient_cookies_to_our_server(self, popup_js):
        assert "credentials: 'omit'" in popup_js

    def test_the_popup_never_renders_a_cookie_value(self, popup_code):
        # Values may only reach `body`; nothing assigns one to the DOM.
        for sink in ("textContent =", "innerHTML =", ".value ="):
            for line in popup_code.splitlines():
                if sink in line:
                    assert "swid" not in line.lower()
                    assert "espnS2" not in line

    def test_the_bookmarklet_is_honest_about_httponly(self):
        source = (EXTENSION_DIR / "bookmarklet" / "bookmarklet.js").read_text()
        assert "HttpOnly" in source
        # It must not claim to read espn_s2 -- it cannot.
        assert "cannot read" in source.lower()
