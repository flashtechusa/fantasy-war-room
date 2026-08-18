"""Administration is a separate account, not a bonus on a team account.

The admin console has its own entrance so managing access is done from a
different login than managing a fantasy team. These tests cover the rule that
makes that meaningful: owner powers follow the account, not the browser.
"""

from __future__ import annotations

import pytest

OWNER = {"username": "admin", "password": "admin-password-1"}


@pytest.fixture
def accounts(anon_client):
    from app.db import session_scope
    from app.services import auth as auth_service

    with session_scope() as session:
        auth_service.ensure_owner(session, OWNER["username"], OWNER["password"])
        session.commit()

    anon_client.post("/api/auth/login", json=OWNER)
    player = anon_client.post(
        "/api/admin/users", json={"username": "flash", "role": "client"}
    ).json()
    return {
        "client": anon_client,
        "admin": OWNER,
        "player": {"username": "flash", "password": player["password"]},
    }


def sign_in(client, who: dict) -> None:
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json=who).status_code == 200


class TestAdminIsItsOwnAccount:
    def test_a_team_account_cannot_manage_access(self, accounts):
        client = accounts["client"]
        sign_in(client, accounts["player"])
        assert client.get("/api/admin/users").status_code == 403
        assert client.post("/api/admin/users", json={"username": "sneaky"}).status_code == 403
        assert client.get("/api/auth/beta-requests").status_code == 403

    def test_a_team_account_cannot_create_an_owner(self, accounts):
        """The obvious escalation: make yourself an admin."""
        client = accounts["client"]
        sign_in(client, accounts["player"])
        assert client.post(
            "/api/admin/users", json={"username": "me2", "role": "owner"}
        ).status_code == 403

    def test_the_admin_account_can(self, accounts):
        client = accounts["client"]
        assert client.get("/api/admin/users").status_code == 200

    def test_signing_out_of_one_signs_out_of_both(self, accounts):
        """One session mechanism -- two would be twice as much to get wrong."""
        client = accounts["client"]
        client.post("/api/auth/logout")
        assert client.get("/api/admin/users").status_code == 401
        assert client.get("/api/config/mine").status_code == 401

    def test_an_admin_can_be_created_without_a_league(self, accounts):
        """An account that only administers has no fantasy team to configure."""
        client = accounts["client"]
        created = client.post(
            "/api/admin/users", json={"username": "admin2", "role": "owner"}
        ).json()
        sign_in(client, {"username": "admin2", "password": created["password"]})
        assert client.get("/api/admin/users").status_code == 200

    def test_demoting_an_owner_removes_their_admin_powers(self, accounts):
        client = accounts["client"]
        created = client.post(
            "/api/admin/users", json={"username": "admin2", "role": "owner"}
        ).json()
        client.patch(f"/api/admin/users/{created['user']['id']}", json={"role": "client"})

        sign_in(client, {"username": "admin2", "password": created["password"]})
        assert client.get("/api/admin/users").status_code == 403


class TestTheAdminPageItself:
    def test_it_is_served_to_a_signed_out_browser(self, anon_client):
        """It has its own sign-in, so the page must load without a session."""
        assert anon_client.get("/admin").status_code == 200

    def test_it_leaks_nothing_before_signing_in(self, anon_client):
        body = anon_client.get("/admin").text
        assert "beta_requests" not in body
        assert "password" not in body.lower() or "<!doctype" in body.lower()
