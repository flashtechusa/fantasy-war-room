"""The ESPN Connection Assistant, end to end through the API.

The property under test throughout is that cookies travel one way. They go in
through `/api/espn/credentials` (or the extension endpoint) and no response
body, anywhere, contains one afterwards.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import espn_connect

from test_espn_discovery import (  # reuse the realistic ESPN fixtures
    MY_SWID,
    fan_entry,
    fan_profile,
    league_payload,
)

S2 = "AEB" + "x9Kq2Lm" * 30


@pytest.fixture
def espn_stub(monkeypatch):
    """Intercept every ESPN request the connect flow makes.

    Returns a controller so a test can decide what ESPN "has" and inspect what
    was actually sent -- including whether the cookies went out as a header.
    """

    class Stub:
        def __init__(self) -> None:
            self.leagues: dict[int, dict] = {}
            self.fan: dict = fan_profile()
            self.requests: list[httpx.Request] = []
            self.fan_status = 200

        def handler(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            url = str(request.url)
            if "fan.api.espn.com" in url:
                if self.fan_status != 200:
                    return httpx.Response(self.fan_status, json={})
                return httpx.Response(200, json=self.fan)
            league_id = int(url.split("/leagues/")[1].split("?")[0])
            if league_id not in self.leagues:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self.leagues[league_id])

        @property
        def cookie_headers(self) -> list[str]:
            return [r.headers.get("Cookie", "") for r in self.requests]

    stub = Stub()
    real = espn_connect.EspnHttpClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(stub.handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(espn_connect, "EspnHttpClient", factory)
    return stub


def connect_credentials(client, swid: str = MY_SWID, s2: str = S2, season: int = 2026):
    return client.post(
        "/api/espn/credentials", json={"swid": swid, "espn_s2": s2, "season": season}
    )


class TestCredentialSubmission:
    def test_credentials_are_stored_and_never_echoed(self, client):
        response = connect_credentials(client)
        assert response.status_code == 201
        body = response.json()
        assert body["stored"] is True
        assert MY_SWID not in str(body)
        assert S2 not in str(body)
        assert body["status"]["swid_set"] is True
        assert body["status"]["espn_s2_set"] is True

    def test_no_endpoint_returns_the_values_back(self, client):
        connect_credentials(client)
        for path in ("/api/espn/status", "/api/config/mine", "/api/health"):
            text = client.get(path).text
            assert MY_SWID not in text
            assert S2 not in text

    def test_they_are_encrypted_at_rest(self, client):
        connect_credentials(client)
        from app.db import session_scope
        from app.models import UserEspnConfig

        with session_scope() as session:
            config = session.query(UserEspnConfig).one()
            # Ciphertext, not the cookie.
            assert MY_SWID not in config.espn_swid_encrypted
            assert S2 not in config.espn_s2_encrypted
            assert config.espn_swid_encrypted
            assert config.espn_s2_encrypted

    def test_both_cookies_are_required(self, client):
        assert client.post("/api/espn/credentials", json={"swid": MY_SWID}).status_code == 422
        assert (
            client.post("/api/espn/credentials", json={"espn_s2": S2}).status_code == 422
        )

    def test_unknown_fields_are_rejected(self, client):
        response = client.post(
            "/api/espn/credentials",
            json={"swid": MY_SWID, "espn_s2": S2, "surprise": "value"},
        )
        assert response.status_code == 422

    def test_a_signed_out_browser_cannot_submit(self, anon_client):
        assert connect_credentials(anon_client).status_code == 401

    def test_status_starts_empty(self, client):
        body = client.get("/api/espn/status").json()
        assert body["credentials_stored"] is False
        assert body["connected"] is False
        assert body["manual_entry_available"] is True


class TestDiscoveryThroughTheApi:
    def test_a_multi_league_account_gets_a_picker(self, client, espn_stub):
        """"Found 3 ESPN leagues" -- the case the flow exists for."""
        names = {111: "Stephen's League", 222: "Work League", 333: "Dynasty League"}
        espn_stub.fan = fan_profile(
            fan_entry(111, names[111]),
            fan_entry(222, names[222], team_id=7, size=10),
            fan_entry(333, names[333], team_id=1),
        )
        espn_stub.leagues = {
            111: league_payload(111, names[111], my_team=3),
            222: league_payload(222, names[222], size=10, my_team=7),
            333: league_payload(333, names[333], my_team=1),
        }
        connect_credentials(client)

        body = client.get("/api/espn/leagues?season=2026").json()
        assert body["count"] == 3
        assert {lg["name"] for lg in body["leagues"]} == set(names.values())
        assert {lg["my_team_id"] for lg in body["leagues"]} == {1, 3, 7}
        assert body["warnings"] == []

    def test_a_single_league_account_still_works(self, client, espn_stub):
        espn_stub.fan = fan_profile(fan_entry(111, "Only League"))
        espn_stub.leagues = {111: league_payload(111, "Only League")}
        connect_credentials(client)
        body = client.get("/api/espn/leagues?season=2026").json()
        assert body["count"] == 1

    def test_discovery_sends_the_cookies_as_a_header(self, client, espn_stub):
        espn_stub.fan = fan_profile(fan_entry(111, "League"))
        espn_stub.leagues = {111: league_payload(111, "League")}
        connect_credentials(client)
        client.get("/api/espn/leagues?season=2026")
        assert all("espn_s2=" in header for header in espn_stub.cookie_headers)
        assert all("SWID=" in header for header in espn_stub.cookie_headers)

    def test_discovery_needs_credentials_first(self, client, espn_stub):
        response = client.get("/api/espn/leagues?season=2026")
        assert response.status_code == 502
        assert "connect espn" in response.json()["detail"].lower()

    def test_a_manual_league_id_is_confirmed_the_same_way(self, client, espn_stub):
        """The fallback runs the same code path, not a second one."""
        espn_stub.fan = fan_profile()  # ESPN lists nothing
        espn_stub.leagues = {987654: league_payload(987654, "Typed by hand")}
        connect_credentials(client)
        body = client.get("/api/espn/leagues?season=2026&league_id=987654").json()
        assert body["count"] == 1
        assert body["leagues"][0]["name"] == "Typed by hand"

    def test_an_empty_account_says_so_rather_than_failing_silently(self, client, espn_stub):
        connect_credentials(client)
        body = client.get("/api/espn/leagues?season=2026").json()
        assert body["count"] == 0
        assert body["warnings"]

    def test_preview_returns_the_rules_to_confirm(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Rules League")}
        connect_credentials(client)
        body = client.get("/api/espn/leagues/111?season=2026").json()
        assert body["league"]["name"] == "Rules League"
        assert body["rules"]["roster_slots"]["RB"] == 2
        assert body["rules"]["uses_faab"] is True
        assert body["rules"]["playoff_team_count"] == 6

    def test_an_unreadable_league_is_a_502_with_a_redacted_message(self, client, espn_stub):
        connect_credentials(client)
        response = client.get("/api/espn/leagues/404404?season=2026")
        assert response.status_code == 502
        assert MY_SWID not in response.text
        assert S2 not in response.text


class TestSelection:
    def test_the_team_is_detected_from_the_swid(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Test", my_team=5)}
        connect_credentials(client)
        body = client.post(
            "/api/espn/select", json={"league_id": 111, "season": 2026}
        ).json()
        assert body["my_team_id"] == 5
        assert body["team_auto_detected"] is True

    def test_an_explicit_team_wins_over_detection(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Test", my_team=5)}
        connect_credentials(client)
        body = client.post(
            "/api/espn/select", json={"league_id": 111, "season": 2026, "team_id": 9}
        ).json()
        assert body["my_team_id"] == 9
        assert body["team_auto_detected"] is False

    def test_a_team_that_is_not_in_the_league_is_rejected(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Test", size=10)}
        connect_credentials(client)
        response = client.post(
            "/api/espn/select", json={"league_id": 111, "season": 2026, "team_id": 40}
        )
        assert response.status_code == 400

    def test_selection_is_persisted_against_this_user(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Test", my_team=5)}
        connect_credentials(client)
        client.post("/api/espn/select", json={"league_id": 111, "season": 2026})
        status = client.get("/api/espn/status").json()
        assert status["connected"] is True
        assert status["espn_league_id"] == 111
        assert status["my_team_id"] == 5

    def test_import_refuses_before_a_league_is_chosen(self, client, espn_stub):
        connect_credentials(client)
        assert client.post("/api/espn/import").status_code == 409


class TestPerUserIsolation:
    def test_two_accounts_hold_separate_connections(self, client, espn_stub):
        """One install, two people, two leagues -- and neither sees the other."""
        espn_stub.leagues = {
            111: league_payload(111, "Mine", my_team=5),
            222: league_payload(222, "Theirs", my_team=2),
        }
        connect_credentials(client)
        client.post("/api/espn/select", json={"league_id": 111, "season": 2026})

        created = client.post(
            "/api/admin/users", json={"username": "second", "role": "client"}
        ).json()

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app, base_url="https://testserver") as other:
            other.post(
                "/api/auth/login",
                json={"username": "second", "password": created["password"]},
            )
            # A fresh account sees nothing of the first one's connection.
            assert other.get("/api/espn/status").json()["credentials_stored"] is False

            other_swid = "{99999999-8888-7777-6666-555555555555}"
            connect_credentials(other, swid=other_swid)
            other.post("/api/espn/select", json={"league_id": 222, "season": 2026})
            assert other.get("/api/espn/status").json()["espn_league_id"] == 222

        assert client.get("/api/espn/status").json()["espn_league_id"] == 111


class TestDisconnect:
    def test_disconnect_deletes_the_stored_credentials(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Test")}
        connect_credentials(client)
        client.post("/api/espn/select", json={"league_id": 111, "season": 2026})

        body = client.delete("/api/espn").json()
        assert body["disconnected"] is True
        assert body["credentials_deleted"] is True
        assert body["league_cleared"] is True
        assert body["status"]["credentials_stored"] is False
        assert body["status"]["espn_league_id"] is None

    def test_the_row_is_gone_from_the_database(self, client, espn_stub):
        connect_credentials(client)
        client.delete("/api/espn")
        from app.db import session_scope
        from app.models import UserEspnConfig

        with session_scope() as session:
            assert session.query(UserEspnConfig).count() == 0

    def test_discovery_stops_working_after_disconnecting(self, client, espn_stub):
        espn_stub.leagues = {111: league_payload(111, "Test")}
        connect_credentials(client)
        assert client.get("/api/espn/leagues?season=2026").status_code == 200
        client.delete("/api/espn")
        assert client.get("/api/espn/leagues?season=2026").status_code == 502

    def test_disconnecting_twice_is_not_an_error(self, client):
        assert client.delete("/api/espn").status_code == 200
        assert client.delete("/api/espn").status_code == 200

    def test_imported_league_data_survives(self, client, espn_stub):
        """A disconnect revokes access; it does not wipe your draft board."""
        client.post("/api/league/import")
        connect_credentials(client)
        client.delete("/api/espn")
        assert client.get("/api/health").json()["league_imported"] is True
