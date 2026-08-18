"""A client account must not be shown the owner's league.

Reported from real use: signing in as a new client account in a private window
still showed the owner's team on /week. The config endpoints were isolated and
tested; the *data* endpoints were not, and they leaked through two separate
fallbacks -- settings inheriting the install-wide connection, and the league
lookup returning "whatever was imported most recently" when nothing matched.
"""

from __future__ import annotations

import pytest

OWNER = {"username": "owner", "password": "owner-password-1"}

#: Every screen a signed-in person can reach that shows league data.
DATA_ENDPOINTS = [
    "/api/league",
    "/api/players",
    "/api/team",
    "/api/team/league",
    "/api/season/lineup",
    "/api/season/roster",
    "/api/season/waivers",
    "/api/draft",
]


@pytest.fixture
def owner_with_league(anon_client):
    """An owner with a real (demo-backed) league imported, plus a client."""
    from app.db import session_scope
    from app.services import auth as auth_service

    with session_scope() as session:
        auth_service.ensure_owner(session, OWNER["username"], OWNER["password"])
        session.commit()

    anon_client.post("/api/auth/login", json=OWNER)
    anon_client.post("/api/league/import")
    assert anon_client.get("/api/league").status_code == 200

    created = anon_client.post(
        "/api/admin/users", json={"username": "ari", "role": "client"}
    ).json()
    return {
        "client": anon_client,
        "owner": OWNER,
        "ari": {"username": "ari", "password": created["password"]},
    }


def sign_in(client, who: dict) -> None:
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json=who).status_code == 200


class TestAClientSeesNothingOfTheOwners:
    @pytest.mark.parametrize("path", DATA_ENDPOINTS)
    def test_no_league_data_is_served(self, owner_with_league, path):
        client = owner_with_league["client"]
        sign_in(client, owner_with_league["ari"])
        response = client.get(path)
        # 404/409 mean "you have no league" -- which is the correct answer.
        assert response.status_code in (404, 409), (
            f"{path} returned {response.status_code} to an account with no league"
        )

    def test_the_owners_league_name_never_appears(self, owner_with_league):
        client = owner_with_league["client"]
        sign_in(client, owner_with_league["owner"])
        league_name = client.get("/api/league").json()["name"]

        sign_in(client, owner_with_league["ari"])
        for path in DATA_ENDPOINTS:
            assert league_name not in client.get(path).text

    def test_health_does_not_advertise_the_owners_league(self, owner_with_league):
        client = owner_with_league["client"]
        sign_in(client, owner_with_league["owner"])
        league_name = client.get("/api/league").json()["name"]

        sign_in(client, owner_with_league["ari"])
        assert league_name not in client.get("/api/health").text

    def test_the_owner_still_sees_their_own(self, owner_with_league):
        """The fix must not lock the owner out of their own league."""
        client = owner_with_league["client"]
        sign_in(client, owner_with_league["owner"])
        assert client.get("/api/league").status_code == 200
        assert client.get("/api/team").status_code == 200


class TestLeagueLookupIsStrict:
    def test_no_configured_league_means_no_league(self, owner_with_league):
        """It used to return whatever had been imported most recently."""
        from app.config import Settings
        from app.db import session_scope
        from app.services.importer import get_active_league

        with session_scope() as session:
            nothing_configured = Settings(
                _env_file=None, espn_league_id=None, demo_mode=False
            )
            assert get_active_league(session, nothing_configured) is None

    def test_a_different_league_id_does_not_match(self, owner_with_league):
        from app.config import Settings
        from app.db import session_scope
        from app.services.importer import get_active_league

        with session_scope() as session:
            someone_elses = Settings(
                _env_file=None, espn_league_id=987654, espn_season=2026, demo_mode=False
            )
            assert get_active_league(session, someone_elses) is None


class TestOnceTheyConnectTheirOwn:
    def test_a_client_with_their_own_league_is_served_it(self, owner_with_league):
        client = owner_with_league["client"]
        sign_in(client, owner_with_league["ari"])
        client.put("/api/config/mine", json={"espn_league_id": 555111, "espn_season": 2026})

        from app.config import get_settings
        from app.db import session_scope
        from app.models import User
        from app.services.runtime_config import settings_for_user

        with session_scope() as session:
            ari = session.query(User).filter(User.username == "ari").one()
            resolved = settings_for_user(session, ari, get_settings())
            assert resolved.espn_league_id == 555111

    def test_and_still_not_the_owners_credentials(self, owner_with_league):
        client = owner_with_league["client"]
        sign_in(client, owner_with_league["owner"])
        client.put("/api/config/mine", json={"espn_league_id": 11507, "espn_s2": "owner-cookie"})

        sign_in(client, owner_with_league["ari"])
        client.put("/api/config/mine", json={"espn_league_id": 555111})

        from app.config import get_settings
        from app.db import session_scope
        from app.models import User
        from app.services.runtime_config import settings_for_user

        with session_scope() as session:
            ari = session.query(User).filter(User.username == "ari").one()
            assert settings_for_user(session, ari, get_settings()).espn_s2 != "owner-cookie"
