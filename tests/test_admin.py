"""Owner-only access management."""

from __future__ import annotations

import pytest


@pytest.fixture
def owner(client):
    """The `client` fixture signs in as the bootstrapped owner."""
    return client


class TestPermissions:
    def test_a_signed_out_browser_sees_nothing(self, anon_client):
        assert anon_client.get("/api/admin/users").status_code == 401
        assert anon_client.post("/api/admin/users", json={"username": "x"}).status_code == 401

    def test_a_client_account_cannot_manage_access(self, owner):
        created = owner.post(
            "/api/admin/users", json={"username": "someone", "role": "client"}
        ).json()
        owner.post("/api/auth/logout")
        owner.post(
            "/api/auth/login",
            json={"username": "someone", "password": created["password"]},
        )
        assert owner.get("/api/admin/users").status_code == 403


class TestCreatingAccounts:
    def test_a_new_account_returns_a_password_once(self, owner):
        response = owner.post("/api/admin/users", json={"username": "dave"})
        assert response.status_code == 201
        assert len(response.json()["password"]) >= 12

    def test_the_password_is_never_readable_afterwards(self, owner):
        owner.post("/api/admin/users", json={"username": "dave"})
        assert "password" not in owner.get("/api/admin/users").text

    def test_the_new_account_can_sign_in(self, owner):
        created = owner.post("/api/admin/users", json={"username": "dave"}).json()
        owner.post("/api/auth/logout")
        assert owner.post(
            "/api/auth/login",
            json={"username": "dave", "password": created["password"]},
        ).status_code == 200

    def test_duplicate_usernames_are_refused(self, owner):
        owner.post("/api/admin/users", json={"username": "dave"})
        assert owner.post("/api/admin/users", json={"username": "dave"}).status_code == 409

    def test_an_unknown_role_is_refused(self, owner):
        assert owner.post(
            "/api/admin/users", json={"username": "x", "role": "wizard"}
        ).status_code == 422

    def test_generated_passwords_differ(self, owner):
        a = owner.post("/api/admin/users", json={"username": "alice"}).json()["password"]
        b = owner.post("/api/admin/users", json={"username": "bobby"}).json()["password"]
        assert a != b

    def test_a_one_character_username_is_refused(self, owner):
        assert owner.post("/api/admin/users", json={"username": "a"}).status_code == 422


class TestSwitchingAccountsOff:
    def test_switching_off_blocks_sign_in(self, owner):
        created = owner.post("/api/admin/users", json={"username": "dave"}).json()
        owner.patch(f"/api/admin/users/{created['user']['id']}", json={"enabled": False})
        owner.post("/api/auth/logout")
        assert owner.post(
            "/api/auth/login",
            json={"username": "dave", "password": created["password"]},
        ).status_code == 401

    def test_the_owner_cannot_lock_themselves_out(self, owner):
        me = [u for u in owner.get("/api/admin/users").json()["users"]
              if u["role"] == "owner"][0]
        response = owner.patch(f"/api/admin/users/{me['id']}", json={"enabled": False})
        assert response.status_code == 400

    def test_resetting_a_password_invalidates_the_old_one(self, owner):
        created = owner.post("/api/admin/users", json={"username": "dave"}).json()
        owner.post(f"/api/admin/users/{created['user']['id']}/reset-password")
        owner.post("/api/auth/logout")
        assert owner.post(
            "/api/auth/login",
            json={"username": "dave", "password": created["password"]},
        ).status_code == 401


class TestBetaRequests:
    def test_the_owner_can_read_them(self, owner, anon_client):
        anon_client.post("/api/auth/beta-request", json={"email": "who@example.com"})
        rows = owner.get("/api/auth/beta-requests").json()["requests"]
        assert any(r["email"] == "who@example.com" for r in rows)

    def test_they_can_be_marked_handled(self, owner, anon_client):
        anon_client.post("/api/auth/beta-request", json={"email": "who@example.com"})
        row = owner.get("/api/auth/beta-requests").json()["requests"][0]
        owner.patch(f"/api/admin/beta-requests/{row['id']}?handled=true")
        updated = owner.get("/api/auth/beta-requests").json()["requests"][0]
        assert updated["handled"] is True
