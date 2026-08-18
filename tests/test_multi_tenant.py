"""Two people, one install, two leagues.

Until this existed an account was authentication only: anyone who signed in
landed in the owner's league and could see and change their team. These tests
are what separate "you are allowed in" from "this is your data".
"""

from __future__ import annotations

import pytest


@pytest.fixture
def two_users(anon_client):
    """An owner and a second account, with the owner signed in."""
    from app.db import session_scope
    from app.services import auth as auth_service

    with session_scope() as session:
        auth_service.ensure_owner(session, "owner", "owner-password-1")
        session.commit()

    anon_client.post("/api/auth/login", json={"username": "owner", "password": "owner-password-1"})
    created = anon_client.post(
        "/api/admin/users", json={"username": "dave", "role": "client"}
    ).json()
    return {
        "client": anon_client,
        "owner": {"username": "owner", "password": "owner-password-1"},
        "dave": {"username": "dave", "password": created["password"]},
    }


def sign_in_as(client, who: dict) -> None:
    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json=who)
    assert response.status_code == 200, response.text


class TestCredentialEncryption:
    def test_a_cookie_is_not_stored_in_the_clear(self, two_users):
        client = two_users["client"]
        secret = "AECgbmqx-this-is-the-cookie"
        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_s2": secret})

        from app.db import session_scope
        from app.models import UserEspnConfig

        with session_scope() as session:
            rows = session.query(UserEspnConfig).all()
            assert rows
            for row in rows:
                assert secret not in (row.espn_s2_encrypted or "")

    def test_it_still_decrypts_back_to_the_original(self, two_users):
        from app.services.secrets import decrypt, encrypt

        assert decrypt(encrypt("AECgbmqx-cookie")) == "AECgbmqx-cookie"

    def test_an_unreadable_value_is_treated_as_unset_not_fatal(self):
        from app.services.secrets import decrypt

        assert decrypt("not-valid-ciphertext") == ""

    def test_the_api_never_returns_the_cookie(self, two_users):
        client = two_users["client"]
        secret = "AECgbmqx-never-echo-me"
        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_s2": secret})
        assert secret not in client.get("/api/config/mine").text
        assert client.get("/api/config/mine").json()["espn_s2_set"] is True


class TestSeparateLeagues:
    def test_each_account_keeps_its_own_connection(self, two_users):
        client = two_users["client"]

        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_season": 2026})
        assert client.get("/api/config/mine").json()["espn_league_id"] == 11507

        sign_in_as(client, two_users["dave"])
        client.put("/api/config/mine", json={"espn_league_id": 99999, "espn_season": 2026})
        assert client.get("/api/config/mine").json()["espn_league_id"] == 99999

        # ...and the owner's is untouched.
        sign_in_as(client, two_users["owner"])
        assert client.get("/api/config/mine").json()["espn_league_id"] == 11507

    def test_a_new_account_starts_with_no_connection(self, two_users):
        client = two_users["client"]
        sign_in_as(client, two_users["dave"])
        body = client.get("/api/config/mine").json()
        assert body["configured"] is False
        assert body["swid_set"] is False

    def test_one_account_cannot_read_another_ones_settings(self, two_users):
        client = two_users["client"]
        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_s2": "owner-cookie"})

        sign_in_as(client, two_users["dave"])
        body = client.get("/api/config/mine").json()
        assert body["espn_league_id"] is None
        assert body["espn_s2_set"] is False

    def test_settings_resolve_per_user(self, two_users):
        """The dependency the whole app hangs off must be user-specific."""
        from app.config import get_settings
        from app.db import session_scope
        from app.models import User
        from app.services.runtime_config import settings_for_user

        client = two_users["client"]
        client.put("/api/config/mine", json={"espn_league_id": 11507})
        sign_in_as(client, two_users["dave"])
        client.put("/api/config/mine", json={"espn_league_id": 99999})

        with session_scope() as session:
            owner = session.query(User).filter(User.username == "owner").one()
            dave = session.query(User).filter(User.username == "dave").one()
            assert settings_for_user(session, owner, get_settings()).espn_league_id == 11507
            assert settings_for_user(session, dave, get_settings()).espn_league_id == 99999

    def test_only_the_owner_inherits_the_install_wide_connection(self, two_users):
        """This test previously asserted the opposite, and that was the bug.

        Letting every account inherit the install-wide league meant a new
        client with no connection of their own was served the owner's team.
        The owner still inherits it -- that is what keeps a single-user
        install working -- but nobody else does.
        """
        from app.config import get_settings
        from app.db import session_scope
        from app.models import User
        from app.services.runtime_config import settings_for_user

        client = two_users["client"]
        client.put("/api/config", json={"espn_league_id": 4242})

        with session_scope() as session:
            owner = session.query(User).filter(User.username == "owner").one()
            dave = session.query(User).filter(User.username == "dave").one()

            assert settings_for_user(session, owner, get_settings()).espn_league_id == 4242
            assert settings_for_user(session, dave, get_settings()).espn_league_id is None

    def test_a_client_does_not_inherit_the_install_wide_cookies_either(self, two_users):
        from app.config import get_settings
        from app.db import session_scope
        from app.models import User
        from app.services.runtime_config import settings_for_user

        client = two_users["client"]
        client.put("/api/config", json={"espn_league_id": 4242, "espn_s2": "install-cookie"})

        with session_scope() as session:
            dave = session.query(User).filter(User.username == "dave").one()
            resolved = settings_for_user(session, dave, get_settings())
            assert resolved.espn_s2 is None
            assert resolved.espn_swid is None


class TestStillGuarded:
    def test_a_signed_out_browser_cannot_read_or_write_a_connection(self, anon_client):
        assert anon_client.get("/api/config/mine").status_code == 401
        assert anon_client.put("/api/config/mine", json={"espn_league_id": 1}).status_code == 401
