"""The League screen must only ever touch the signed-in user's connection.

These exercise the exact endpoints the screen calls, in the order it calls
them, because the failure being guarded against is not a broken request -- it
is a working one pointed at the wrong record.
"""

from __future__ import annotations

import pytest

OWNER = {"username": "owner", "password": "owner-password-1"}


@pytest.fixture
def accounts(anon_client):
    """An owner plus two client accounts, owner signed in."""
    from app.db import session_scope
    from app.services import auth as auth_service

    with session_scope() as session:
        auth_service.ensure_owner(session, OWNER["username"], OWNER["password"])
        session.commit()

    anon_client.post("/api/auth/login", json=OWNER)
    made = {}
    for name in ("alice", "bobby"):
        created = anon_client.post(
            "/api/admin/users", json={"username": name, "role": "client"}
        ).json()
        made[name] = {"username": name, "password": created["password"]}
    return {"client": anon_client, "owner": OWNER, **made}


def sign_in(client, who: dict) -> None:
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json=who).status_code == 200


class TestWhatTheScreenReads:
    def test_a_new_account_sees_an_empty_connection(self, accounts):
        """The core requirement: no sight of anyone else's league."""
        client = accounts["client"]
        client.put(
            "/api/config/mine",
            json={"espn_league_id": 11507, "espn_season": 2026, "espn_s2": "owner-cookie"},
        )

        sign_in(client, accounts["alice"])
        body = client.get("/api/config/mine").json()
        assert body["configured"] is False
        assert body["espn_league_id"] is None
        assert body["espn_season"] is None
        assert body["swid_set"] is False
        assert body["espn_s2_set"] is False

    def test_a_client_cannot_read_the_install_wide_settings(self, accounts):
        """That endpoint reports the owner's league id."""
        client = accounts["client"]
        sign_in(client, accounts["alice"])
        assert client.get("/api/config").status_code == 403

    def test_the_owner_still_can(self, accounts):
        assert accounts["client"].get("/api/config").status_code == 200


class TestWhatTheScreenWrites:
    def test_saving_only_changes_the_signed_in_account(self, accounts):
        client = accounts["client"]
        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_season": 2026})

        sign_in(client, accounts["alice"])
        client.put("/api/config/mine", json={"espn_league_id": 22222, "espn_season": 2026})

        sign_in(client, accounts["bobby"])
        client.put("/api/config/mine", json={"espn_league_id": 33333, "espn_season": 2026})

        for who, expected in (
            (accounts["owner"], 11507),
            (accounts["alice"], 22222),
            (accounts["bobby"], 33333),
        ):
            sign_in(client, who)
            assert client.get("/api/config/mine").json()["espn_league_id"] == expected

    def test_a_client_cannot_write_the_install_wide_settings(self, accounts):
        client = accounts["client"]
        sign_in(client, accounts["alice"])
        assert client.put("/api/config", json={"espn_league_id": 99}).status_code == 403
        assert client.delete("/api/config").status_code == 403

    def test_install_wide_keys_are_ignored_on_a_personal_save(self, accounts):
        """Otherwise a client could switch demo mode for everyone."""
        client = accounts["client"]
        client.put("/api/config", json={"demo_mode": True})

        sign_in(client, accounts["alice"])
        client.put(
            "/api/config/mine",
            json={"espn_league_id": 4242, "demo_mode": False, "fantasypros_api_key": "stolen"},
        )

        sign_in(client, accounts["owner"])
        install = client.get("/api/config").json()
        assert install["demo_mode"] is True
        assert install["fantasypros_key_set"] is False

    def test_my_team_and_faab_are_per_user(self, accounts):
        client = accounts["client"]
        client.put("/api/config/mine", json={"espn_league_id": 11507, "my_team_id": 7,
                                             "faab_remaining": 100})

        sign_in(client, accounts["alice"])
        client.put("/api/config/mine", json={"espn_league_id": 22222, "my_team_id": 3,
                                             "faab_remaining": 55})
        assert client.get("/api/config/mine").json()["my_team_id"] == 3

        sign_in(client, accounts["owner"])
        mine = client.get("/api/config/mine").json()
        assert mine["my_team_id"] == 7
        assert mine["faab_remaining"] == 100


class TestCredentialsStayPrivate:
    def test_a_cookie_is_never_returned_to_any_account(self, accounts):
        client = accounts["client"]
        secret = "AEC-owner-private-cookie"
        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_s2": secret})

        assert secret not in client.get("/api/config/mine").text

        sign_in(client, accounts["alice"])
        assert secret not in client.get("/api/config/mine").text

    def test_two_accounts_store_different_cookies_independently(self, accounts):
        client = accounts["client"]
        client.put("/api/config/mine", json={"espn_league_id": 1, "espn_s2": "owner-cookie"})

        sign_in(client, accounts["alice"])
        client.put("/api/config/mine", json={"espn_league_id": 2, "espn_s2": "alice-cookie"})

        from app.config import get_settings
        from app.db import session_scope
        from app.models import User
        from app.services.runtime_config import settings_for_user

        with session_scope() as session:
            owner = session.query(User).filter(User.username == "owner").one()
            alice = session.query(User).filter(User.username == "alice").one()
            assert settings_for_user(session, owner, get_settings()).espn_s2 == "owner-cookie"
            assert settings_for_user(session, alice, get_settings()).espn_s2 == "alice-cookie"

    def test_clearing_one_account_leaves_the_other_alone(self, accounts):
        client = accounts["client"]
        client.put("/api/config/mine", json={"espn_league_id": 1, "espn_s2": "owner-cookie"})

        sign_in(client, accounts["alice"])
        client.put("/api/config/mine", json={"espn_league_id": 2, "espn_s2": "alice-cookie"})
        client.put("/api/config/mine", json={"espn_s2": ""})
        assert client.get("/api/config/mine").json()["espn_s2_set"] is False

        sign_in(client, accounts["owner"])
        assert client.get("/api/config/mine").json()["espn_s2_set"] is True


class TestSignedOut:
    def test_neither_endpoint_answers_without_a_session(self, anon_client):
        assert anon_client.get("/api/config/mine").status_code == 401
        assert anon_client.put("/api/config/mine", json={"espn_league_id": 1}).status_code == 401
        assert anon_client.get("/api/config").status_code == 401
