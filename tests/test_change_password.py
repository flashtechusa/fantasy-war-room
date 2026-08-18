"""Changing your own password.

A new account arrives with a generated password that had to be sent through
some chat window to reach its owner. Without a way to replace it, that
password stays valid forever wherever it was pasted.
"""

from __future__ import annotations

import pytest

OWNER = {"username": "owner", "password": "owner-password-1"}


@pytest.fixture
def signed_in(anon_client):
    from app.db import session_scope
    from app.services import auth as auth_service

    with session_scope() as session:
        auth_service.ensure_owner(session, OWNER["username"], OWNER["password"])
        session.commit()
    assert anon_client.post("/api/auth/login", json=OWNER).status_code == 200
    return anon_client


class TestChangingIt:
    def test_the_new_password_works(self, signed_in):
        assert signed_in.post(
            "/api/auth/change-password",
            json={"current_password": OWNER["password"], "new_password": "a-brand-new-one"},
        ).status_code == 200

        signed_in.post("/api/auth/logout")
        assert signed_in.post(
            "/api/auth/login",
            json={"username": "owner", "password": "a-brand-new-one"},
        ).status_code == 200

    def test_the_old_password_stops_working(self, signed_in):
        signed_in.post(
            "/api/auth/change-password",
            json={"current_password": OWNER["password"], "new_password": "a-brand-new-one"},
        )
        signed_in.post("/api/auth/logout")
        assert signed_in.post("/api/auth/login", json=OWNER).status_code == 401

    def test_you_stay_signed_in_afterwards(self, signed_in):
        """Changing a password should not eject the browser doing it."""
        signed_in.post(
            "/api/auth/change-password",
            json={"current_password": OWNER["password"], "new_password": "a-brand-new-one"},
        )
        assert signed_in.get("/api/auth/me").json()["authenticated"] is True

    def test_the_wrong_current_password_is_refused(self, signed_in):
        response = signed_in.post(
            "/api/auth/change-password",
            json={"current_password": "not-it", "new_password": "a-brand-new-one"},
        )
        assert response.status_code == 401
        # ...and the real password still works.
        signed_in.post("/api/auth/logout")
        assert signed_in.post("/api/auth/login", json=OWNER).status_code == 200

    def test_reusing_the_same_password_is_refused(self, signed_in):
        assert signed_in.post(
            "/api/auth/change-password",
            json={
                "current_password": OWNER["password"],
                "new_password": OWNER["password"],
            },
        ).status_code == 400

    def test_a_short_password_is_refused(self, signed_in):
        assert signed_in.post(
            "/api/auth/change-password",
            json={"current_password": OWNER["password"], "new_password": "short"},
        ).status_code == 422

    def test_a_generated_password_is_long_enough_to_be_kept(self):
        """The minimum must not reject the passwords the app itself issues."""
        from app.api.routes_admin import generate_password

        assert len(generate_password()) >= 10

    def test_signed_out_browsers_cannot_change_anything(self, anon_client):
        assert anon_client.post(
            "/api/auth/change-password",
            json={"current_password": "x", "new_password": "yyyyyyyyyy"},
        ).status_code == 401


class TestOtherSessions:
    def test_other_sessions_are_ended(self, signed_in, anon_client):
        """If the password is being changed because it leaked, old sessions
        surviving would defeat the point."""
        from app.db import session_scope
        from app.models import AuthSession, User

        with session_scope() as session:
            user = session.query(User).filter(User.username == "owner").one()
            from app.services.auth import create_session

            create_session(session, user, "another-browser")
            session.commit()
            assert session.query(AuthSession).filter(
                AuthSession.user_id == user.id
            ).count() == 2

        signed_in.post(
            "/api/auth/change-password",
            json={"current_password": OWNER["password"], "new_password": "a-brand-new-one"},
        )

        with session_scope() as session:
            user = session.query(User).filter(User.username == "owner").one()
            # Only the browser that made the change survives.
            assert session.query(AuthSession).filter(
                AuthSession.user_id == user.id
            ).count() == 1
