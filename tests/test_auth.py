"""Sign-in, the public landing page, and what stays private.

The app used to sit behind the web server's password prompt, which covered
every path -- so a public landing page was impossible. Access control now lives
here, which means these tests are what stands between the internet and the data.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def account(anon_client):
    """An owner account, created the way the app bootstraps one."""
    from app.db import session_scope
    from app.services import auth as auth_service

    with session_scope() as session:
        auth_service.ensure_owner(session, "flash", "pw-fixture-Zx7q!")
        session.commit()
    return {"username": "flash", "password": "pw-fixture-Zx7q!"}


class TestPasswordHashing:
    def test_a_password_verifies_against_its_own_hash(self):
        from app.services.auth import hash_password, verify_password

        assert verify_password("pw-fixture-Zx7q!", hash_password("pw-fixture-Zx7q!"))

    def test_a_wrong_password_does_not(self):
        from app.services.auth import hash_password, verify_password

        assert not verify_password("wrong", hash_password("pw-fixture-Zx7q!"))

    def test_the_password_is_not_recoverable_from_the_hash(self):
        from app.services.auth import hash_password

        assert "pw-fixture-Zx7q!" not in hash_password("pw-fixture-Zx7q!")

    def test_the_same_password_hashes_differently_each_time(self):
        """Per-user salt: identical passwords must not share a hash."""
        from app.services.auth import hash_password

        assert hash_password("same") != hash_password("same")

    def test_malformed_stored_hashes_are_rejected_not_crashed_on(self):
        from app.services.auth import verify_password

        for bad in ("", "nonsense", "scrypt$zz$zz", "md5$a$b"):
            assert verify_password("x", bad) is False


class TestPublicSurface:
    def test_the_landing_page_is_reachable_signed_out(self, anon_client):
        assert anon_client.get("/").status_code == 200

    def test_health_stays_public_for_uptime_checks(self, anon_client):
        assert anon_client.get("/api/health").status_code == 200

    def test_me_answers_signed_out_rather_than_erroring(self, anon_client):
        body = anon_client.get("/api/auth/me").json()
        assert body["authenticated"] is False
        assert body["user"] is None

    def test_anyone_can_request_access(self, anon_client):
        response = anon_client.post(
            "/api/auth/beta-request", json={"email": "someone@example.com"}
        )
        assert response.status_code == 201

    def test_a_bad_address_is_refused(self, anon_client):
        assert anon_client.post(
            "/api/auth/beta-request", json={"email": "not-an-address"}
        ).status_code == 422

    def test_asking_twice_does_not_reveal_the_first_time(self, anon_client):
        """Whether an address already asked is not public information."""
        first = anon_client.post("/api/auth/beta-request", json={"email": "a@b.com"})
        second = anon_client.post("/api/auth/beta-request", json={"email": "a@b.com"})
        assert first.json() == second.json()

    def test_there_is_no_registration_endpoint(self, anon_client):
        """Access is granted by the owner; nothing self-service exists."""
        for path in ("/api/auth/register", "/api/auth/signup", "/api/users"):
            # 401 from the guard is as good as 404 here -- what matters is that
            # no request to any of these ever creates an account.
            assert anon_client.post(path, json={}).status_code in (401, 404, 405)


class TestPrivateSurface:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/league",
            "/api/players",
            "/api/team",
            "/api/team/league",
            "/api/season/lineup",
            "/api/season/roster",
            "/api/config",
        ],
    )
    def test_league_data_requires_signing_in(self, anon_client, path):
        assert anon_client.get(path).status_code == 401

    def test_configuration_cannot_be_changed_signed_out(self, anon_client):
        assert anon_client.put("/api/config", json={"espn_league_id": 1}).status_code == 401

    def test_beta_requests_are_not_readable_signed_out(self, anon_client):
        assert anon_client.get("/api/auth/beta-requests").status_code == 401


class TestSigningIn:
    def test_correct_credentials_sign_you_in(self, anon_client, account):
        response = anon_client.post("/api/auth/login", json=account)
        assert response.status_code == 200
        assert response.json()["authenticated"] is True

    def test_a_session_cookie_is_set_and_not_readable_by_scripts(self, anon_client, account):
        response = anon_client.post("/api/auth/login", json=account)
        cookie = response.headers.get("set-cookie", "")
        assert "fwr_session=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie

    def test_signing_in_unlocks_the_data(self, anon_client, account):
        anon_client.post("/api/auth/login", json=account)
        assert anon_client.get("/api/health").status_code == 200
        assert anon_client.get("/api/config").status_code == 200

    def test_a_wrong_password_is_refused(self, anon_client, account):
        response = anon_client.post(
            "/api/auth/login", json={"username": "flash", "password": "nope"}
        )
        assert response.status_code == 401

    def test_an_unknown_user_gets_the_same_message_as_a_wrong_password(
        self, anon_client, account
    ):
        """Otherwise the login form doubles as a way to enumerate usernames."""
        wrong_password = anon_client.post(
            "/api/auth/login", json={"username": "flash", "password": "nope"}
        )
        no_such_user = anon_client.post(
            "/api/auth/login", json={"username": "ghost", "password": "nope"}
        )
        assert wrong_password.json()["detail"] == no_such_user.json()["detail"]

    def test_signing_out_locks_it_again(self, anon_client, account):
        anon_client.post("/api/auth/login", json=account)
        assert anon_client.get("/api/config").status_code == 200
        anon_client.post("/api/auth/logout")
        assert anon_client.get("/api/config").status_code == 401

    def test_a_disabled_account_cannot_sign_in(self, anon_client, account):
        from app.db import session_scope
        from app.models import User

        with session_scope() as session:
            session.query(User).filter(User.username == "flash").update({"enabled": False})
            session.commit()
        assert anon_client.post("/api/auth/login", json=account).status_code == 401

    def test_disabling_an_account_ends_its_live_session(self, anon_client, account):
        """Switching someone off has to take effect now, not at expiry."""
        anon_client.post("/api/auth/login", json=account)
        assert anon_client.get("/api/config").status_code == 200

        from app.db import session_scope
        from app.models import User

        with session_scope() as session:
            session.query(User).filter(User.username == "flash").update({"enabled": False})
            session.commit()

        assert anon_client.get("/api/config").status_code == 401


class TestOwnerBootstrap:
    def test_the_first_account_can_be_created_from_configuration(self, anon_client):
        from app.db import session_scope
        from app.services.auth import ensure_owner

        with session_scope() as session:
            user = ensure_owner(session, "owner", "secret123")
            session.commit()
            assert user is not None
            assert user.role == "owner"

    def test_it_does_not_overwrite_an_existing_account(self, anon_client, account):
        """Changing the config later must not silently reset a password."""
        from app.db import session_scope
        from app.services.auth import ensure_owner

        with session_scope() as session:
            same = ensure_owner(session, "different", "other")
            session.commit()
            assert same.username == "flash"

        assert anon_client.post("/api/auth/login", json=account).status_code == 200
