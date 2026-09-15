"""Accounts, connections, and the isolation between them.

The failure this suite exists to catch is the quiet one: with a single user and
a single connection everything looks correct whether or not the queries are
scoped, and the bug only appears when a second person signs up. So most of
these tests are about what one account *cannot* see or do.

The rest cover the other half of the ask: one person holding an ESPN league and
a Yahoo league at the same time and switching between them.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def multi_user(monkeypatch):
    """Turn on accounts, as a hosted deployment would."""
    monkeypatch.setenv("FWR_MULTI_USER", "true")
    # The test client speaks plain http, so a Secure cookie would never come back.
    monkeypatch.setenv("FWR_SECURE_COOKIES", "false")
    from app import config

    config.reset_settings_cache()
    yield
    config.reset_settings_cache()


@pytest.fixture
def hosted_client(multi_user, tmp_path):
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app

    init_db()
    with TestClient(app) as client:
        yield client


def _register(client, email: str, password: str = "correct-horse") -> None:
    response = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text


class TestSingleUserInstall:
    """The default. No login, no friction, nothing to configure."""

    def test_there_is_no_sign_in(self, client):
        body = client.get("/api/auth/me").json()
        assert body["multi_user"] is False
        assert body["user"]["is_local"] is True
        # A local account has no address to sign in with, and none is exposed.
        assert body["user"]["email"] == ""

    def test_signing_in_is_refused_rather_than_half_working(self, client):
        response = client.post(
            "/api/auth/login", json={"email": "someone@example.com", "password": "whatever"}
        )
        assert response.status_code == 409
        assert "FWR_MULTI_USER" in response.json()["detail"]

    def test_the_environment_seeds_a_connection(self, client):
        """A `.env` that already names a league should not need re-entering."""
        body = client.get("/api/auth/me").json()
        assert len(body["connections"]) == 1
        assert body["connections"][0]["platform"] == "demo"
        assert body["active_connection_id"] == body["connections"][0]["id"]


class TestAuthentication:
    def test_registering_signs_you_in(self, hosted_client):
        _register(hosted_client, "first@example.com")
        body = hosted_client.get("/api/auth/me").json()
        assert body["user"]["email"] == "first@example.com"
        assert body["multi_user"] is True

    def test_the_api_is_closed_without_a_session(self, hosted_client):
        hosted_client.cookies.clear()
        for path in ("/api/auth/me", "/api/connections", "/api/league", "/api/team"):
            assert hosted_client.get(path).status_code == 401, path

    def test_the_health_probe_answers_anonymously_but_says_nothing(self, hosted_client):
        """Deploy platforms probe this without a cookie; a 401 fails the release."""
        _register(hosted_client, "health@example.com")
        hosted_client.post("/api/connections", json={"platform": "demo", "season": 2026})
        hosted_client.post("/api/league/import")
        hosted_client.cookies.clear()

        response = hosted_client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["authenticated"] is False
        # Liveness only: nothing about anyone's league.
        assert body.get("league") is None
        assert body.get("connection") is None

    def test_the_sign_in_screen_can_ask_what_is_allowed(self, hosted_client):
        hosted_client.cookies.clear()
        body = hosted_client.get("/api/auth/config").json()
        assert body["multi_user"] is True
        assert body["allow_registration"] is True

    def test_a_bad_password_is_rejected_without_saying_which_half_was_wrong(
        self, hosted_client
    ):
        _register(hosted_client, "someone@example.com")
        hosted_client.post("/api/auth/logout")

        wrong_password = hosted_client.post(
            "/api/auth/login",
            json={"email": "someone@example.com", "password": "not-the-password"},
        )
        unknown_account = hosted_client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "not-the-password"},
        )
        assert wrong_password.status_code == 401
        assert unknown_account.status_code == 401
        assert wrong_password.json()["detail"] == unknown_account.json()["detail"]

    def test_short_passwords_are_refused(self, hosted_client):
        response = hosted_client.post(
            "/api/auth/register", json={"email": "short@example.com", "password": "abc"}
        )
        assert response.status_code == 422

    def test_duplicate_registration_is_refused(self, hosted_client):
        _register(hosted_client, "twice@example.com")
        hosted_client.post("/api/auth/logout")
        response = hosted_client.post(
            "/api/auth/register", json={"email": "twice@example.com", "password": "another-one"}
        )
        assert response.status_code == 400

    def test_signing_out_ends_the_session(self, hosted_client):
        _register(hosted_client, "out@example.com")
        assert hosted_client.get("/api/auth/me").status_code == 200
        hosted_client.post("/api/auth/logout")
        assert hosted_client.get("/api/auth/me").status_code == 401

    def test_a_password_change_revokes_other_sessions(self, hosted_client, multi_user):
        from fastapi.testclient import TestClient

        from app.main import app

        _register(hosted_client, "rotate@example.com", "first-password")
        stolen = dict(hosted_client.cookies)

        changed = hosted_client.post(
            "/api/auth/password",
            json={"current_password": "first-password", "new_password": "second-password"},
        )
        assert changed.status_code == 200

        # The old cookie, held by someone else, must stop working.
        with TestClient(app) as other:
            other.cookies.update(stolen)
            assert other.get("/api/auth/me").status_code == 401

        # The browser that changed it stays signed in.
        assert hosted_client.get("/api/auth/me").status_code == 200

    def test_the_session_cookie_is_not_readable_by_script(self, hosted_client):
        response = hosted_client.post(
            "/api/auth/register", json={"email": "cookie@example.com", "password": "correct-horse"}
        )
        header = response.headers["set-cookie"]
        assert "httponly" in header.lower()
        assert "samesite=lax" in header.lower()


class TestIsolation:
    """What one account must never be able to see or touch."""

    @pytest.fixture
    def two_accounts(self, hosted_client, multi_user):
        from fastapi.testclient import TestClient

        from app.main import app

        _register(hosted_client, "alice@example.com")
        alice_connection = hosted_client.post(
            "/api/connections",
            json={"platform": "espn", "league_id": 111111, "season": 2026, "label": "Alice"},
        ).json()["connection"]
        hosted_client.post("/api/auth/logout")

        bob = TestClient(app)
        bob.__enter__()
        _register(bob, "bob@example.com")
        bob_connection = bob.post(
            "/api/connections",
            json={"platform": "espn", "league_id": 222222, "season": 2026, "label": "Bob"},
        ).json()["connection"]

        alice = TestClient(app)
        alice.__enter__()
        alice.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "correct-horse"}
        )
        yield (alice, alice_connection, bob, bob_connection)
        alice.__exit__(None, None, None)
        bob.__exit__(None, None, None)

    def test_each_account_sees_only_its_own_connections(self, two_accounts):
        alice, alice_connection, bob, bob_connection = two_accounts

        alice_ids = {c["id"] for c in alice.get("/api/connections").json()["connections"]}
        bob_ids = {c["id"] for c in bob.get("/api/connections").json()["connections"]}

        assert alice_connection["id"] in alice_ids
        assert bob_connection["id"] not in alice_ids
        assert alice_connection["id"] not in bob_ids

    def test_another_accounts_connection_cannot_be_activated(self, two_accounts):
        alice, _, _, bob_connection = two_accounts
        response = alice.post(f"/api/connections/{bob_connection['id']}/activate")
        assert response.status_code == 404

    def test_another_accounts_connection_cannot_be_read_or_changed(self, two_accounts):
        alice, _, _, bob_connection = two_accounts
        assert alice.patch(
            f"/api/connections/{bob_connection['id']}", json={"label": "mine now"}
        ).status_code == 404
        assert alice.delete(f"/api/connections/{bob_connection['id']}").status_code == 404

    def test_two_accounts_can_hold_the_same_league(self, hosted_client, multi_user):
        """The old unique constraint made this a 500; it is a normal case."""
        from fastapi.testclient import TestClient

        from app.main import app

        _register(hosted_client, "one@example.com")
        first = hosted_client.post(
            "/api/connections", json={"platform": "espn", "league_id": 999, "season": 2026}
        )
        assert first.status_code == 201
        hosted_client.post("/api/auth/logout")

        with TestClient(app) as other:
            _register(other, "two@example.com")
            second = other.post(
                "/api/connections", json={"platform": "espn", "league_id": 999, "season": 2026}
            )
            assert second.status_code == 201
            assert second.json()["connection"]["id"] != first.json()["connection"]["id"]

    def test_leagues_and_players_are_scoped_to_a_connection(self, hosted_client, multi_user):
        """Two demo imports under two accounts must not see each other's rows."""
        from fastapi.testclient import TestClient

        from app.db import session_scope
        from app.main import app
        from app.models import Connection, League, Player

        _register(hosted_client, "importer@example.com")
        hosted_client.post("/api/connections", json={"platform": "demo", "season": 2026})
        assert hosted_client.post("/api/league/import").status_code == 201
        hosted_client.post("/api/auth/logout")

        with TestClient(app) as other:
            _register(other, "second@example.com")
            other.post("/api/connections", json={"platform": "demo", "season": 2026})
            assert other.post("/api/league/import").status_code == 201

        with session_scope() as session:
            connection_ids = {c.id for c in session.query(Connection).all()}
            league_owners = {league.connection_id for league in session.query(League).all()}
            player_owners = {player.connection_id for player in session.query(Player).all()}

            # Two separate imports, each fully attributed to its own connection.
            assert len(league_owners) == 2
            assert league_owners <= connection_ids
            assert player_owners == league_owners

    def test_a_signed_in_user_only_sees_their_own_league(self, hosted_client, multi_user):
        from fastapi.testclient import TestClient

        from app.main import app

        _register(hosted_client, "mine@example.com")
        hosted_client.post("/api/connections", json={"platform": "demo", "season": 2026})
        hosted_client.post("/api/league/import")
        mine = hosted_client.get("/api/league").json()
        hosted_client.post("/api/auth/logout")

        with TestClient(app) as stranger:
            _register(stranger, "stranger@example.com")
            # No import of their own: they get "nothing here yet", never my league.
            response = stranger.get("/api/league")
            assert response.status_code == 404
            assert str(mine["id"]) not in response.text


class TestSwitchingPlatforms:
    """One person, an ESPN league and a Yahoo league, switching between them."""

    @pytest.fixture
    def both(self, hosted_client, multi_user):
        _register(hosted_client, "both@example.com")
        espn = hosted_client.post(
            "/api/connections",
            json={"platform": "espn", "league_id": 123456, "season": 2026, "label": "Work league"},
        ).json()["connection"]
        yahoo = hosted_client.post(
            "/api/connections",
            json={"platform": "yahoo", "league_id": 654321, "season": 2026, "label": "Home league"},
        ).json()["connection"]
        return (hosted_client, espn, yahoo)

    def test_both_are_listed(self, both):
        client, espn, yahoo = both
        body = client.get("/api/connections").json()
        platforms = {c["id"]: c["platform"] for c in body["connections"]}
        assert platforms[espn["id"]] == "espn"
        assert platforms[yahoo["id"]] == "yahoo"

    def test_the_most_recently_added_is_active(self, both):
        client, _, yahoo = both
        assert client.get("/api/connections").json()["active_connection_id"] == yahoo["id"]

    def test_switching_changes_which_league_the_app_is_about(self, both):
        client, espn, yahoo = both

        client.post(f"/api/connections/{espn['id']}/activate")
        config = client.get("/api/config").json()
        assert config["platform"] == "espn"
        assert config["espn_league_id"] == 123456
        assert config["connection"]["id"] == espn["id"]

        client.post(f"/api/connections/{yahoo['id']}/activate")
        config = client.get("/api/config").json()
        assert config["platform"] == "yahoo"
        assert config["yahoo_league_id"] == 654321
        # The ESPN league id must not leak across the switch, or the league
        # lookup would filter on an id from the wrong platform.
        assert config["espn_league_id"] is None

    def test_credentials_follow_the_connection(self, both):
        client, espn, yahoo = both

        client.post(f"/api/connections/{espn['id']}/activate")
        client.put("/api/config", json={"espn_swid": "{SWID-VALUE}", "espn_s2": "s2-value"})
        assert client.get("/api/config").json()["swid_set"] is True

        client.post(f"/api/connections/{yahoo['id']}/activate")
        # The Yahoo connection has its own (empty) credentials, not ESPN's.
        assert client.get("/api/config").json()["swid_set"] is False

    def test_removing_one_leaves_the_other_active(self, both):
        client, espn, yahoo = both
        client.delete(f"/api/connections/{yahoo['id']}")
        body = client.get("/api/connections").json()
        assert body["active_connection_id"] == espn["id"]
        assert [c["id"] for c in body["connections"]] == [espn["id"]]

    def test_per_league_settings_do_not_bleed_across(self, both):
        client, espn, yahoo = both

        client.post(f"/api/connections/{espn['id']}/activate")
        client.put("/api/config", json={"my_team_id": 4, "faab_remaining": 60})

        client.post(f"/api/connections/{yahoo['id']}/activate")
        config = client.get("/api/config").json()
        assert config["my_team_id"] is None
        assert config["faab_remaining"] is None

        client.post(f"/api/connections/{espn['id']}/activate")
        config = client.get("/api/config").json()
        assert config["my_team_id"] == 4
        assert config["faab_remaining"] == 60


class TestOperatorBoundary:
    """A hosted install has one operator and many users.

    Anything installation-wide -- the Yahoo developer app, the FantasyPros key,
    demo mode, pulling new code -- affects everybody, so an ordinary account
    must not be able to touch it.
    """

    @pytest.fixture
    def operator_and_member(self, hosted_client, multi_user):
        from fastapi.testclient import TestClient

        from app.main import app

        _register(hosted_client, "operator@example.com")
        hosted_client.post("/api/auth/logout")

        member = TestClient(app)
        member.__enter__()
        _register(member, "member@example.com")

        operator = TestClient(app)
        operator.__enter__()
        operator.post(
            "/api/auth/login",
            json={"email": "operator@example.com", "password": "correct-horse"},
        )
        yield (operator, member)
        operator.__exit__(None, None, None)
        member.__exit__(None, None, None)

    def test_the_first_account_is_the_operator(self, operator_and_member):
        operator, member = operator_and_member
        assert operator.get("/api/auth/me").json()["user"]["is_admin"] is True
        assert member.get("/api/auth/me").json()["user"]["is_admin"] is False

    def test_a_member_cannot_change_installation_settings(self, operator_and_member):
        _, member = operator_and_member
        for payload in (
            {"fantasypros_api_key": "not-yours"},
            {"demo_mode": True},
            {"yahoo_client_id": "hijack", "yahoo_client_secret": "hijack-secret"},
        ):
            response = member.put("/api/config", json=payload)
            assert response.status_code == 403, payload

    def test_a_member_can_still_configure_their_own_league(self, operator_and_member):
        _, member = operator_and_member
        member.post("/api/connections", json={"platform": "espn", "league_id": 777, "season": 2026})
        response = member.put("/api/config", json={"my_team_id": 2, "espn_s2": "their-cookie"})
        assert response.status_code == 200
        assert response.json()["config"]["my_team_id"] == 2

    def test_a_member_cannot_register_the_yahoo_app(self, operator_and_member):
        _, member = operator_and_member
        response = member.put(
            "/api/yahoo/app",
            json={"yahoo_client_id": "member-client", "yahoo_client_secret": "member-secret"},
        )
        assert response.status_code == 403

    def test_a_member_cannot_update_the_server(self, operator_and_member):
        """Self-update pulls code and restarts; that is not a user action."""
        _, member = operator_and_member
        assert member.post("/api/system/update").status_code == 403
        assert member.get("/api/system/version").status_code == 403

    def test_a_member_cannot_reset_the_installation(self, operator_and_member):
        _, member = operator_and_member
        assert member.delete("/api/config").status_code == 403

    def test_the_operator_can(self, operator_and_member):
        operator, _ = operator_and_member
        assert operator.put("/api/config", json={"fantasypros_api_key": "ok"}).status_code == 200
        assert operator.get("/api/config").json()["fantasypros_key_set"] is True

    def test_a_single_user_install_owns_itself(self, client):
        """Nobody should have to grant themselves admin on their own laptop."""
        assert client.get("/api/auth/me").json()["user"]["is_admin"] is True
        assert client.put("/api/config", json={"demo_mode": True}).status_code == 200


class TestInstallationSettings:
    """The Yahoo app belongs to the operator, not to one user."""

    def test_the_yahoo_app_is_shared_across_accounts(self, hosted_client, multi_user):
        from fastapi.testclient import TestClient

        from app.main import app

        _register(hosted_client, "operator@example.com")  # first account: the operator
        hosted_client.put(
            "/api/yahoo/app",
            json={
                "yahoo_client_id": "client-id-value",
                "yahoo_client_secret": "client-secret-value",
            },
        )
        hosted_client.post("/api/auth/logout")

        with TestClient(app) as member:
            _register(member, "member@example.com")
            body = member.get("/api/config").json()
            # Every user can use the installation's app registration...
            assert body["yahoo_app_configured"] is True
            # ...without it ever being handed back to a browser.
            assert "client-secret-value" not in str(body)

    def test_yahoo_tokens_stay_with_the_user_who_authorised(
        self, hosted_client, multi_user
    ):
        from fastapi.testclient import TestClient

        from app.db import session_scope
        from app.main import app
        from app.models import Connection
        from app.services import accounts, connections

        _register(hosted_client, "yahoo-user@example.com")
        hosted_client.post(
            "/api/connections", json={"platform": "yahoo", "league_id": 4242, "season": 2026}
        )

        with session_scope() as session:
            user = accounts.get_user_by_email(session, "yahoo-user@example.com")
            connection = connections.active_connection(session, user)
            connections.apply_values(
                session,
                connection,
                {
                    "yahoo_access_token": "private-access-token",
                    "yahoo_refresh_token": "private-refresh-token",
                },
            )

        assert hosted_client.get("/api/config").json()["yahoo_connected"] is True
        hosted_client.post("/api/auth/logout")

        with TestClient(app) as other:
            _register(other, "nosy@example.com")
            body = other.get("/api/config").json()
            assert body["yahoo_connected"] is False
            assert "private-access-token" not in str(body)

        with session_scope() as session:
            holders = {
                row.yahoo_access_token
                for row in session.query(Connection).all()
                if row.yahoo_access_token
            }
            assert holders == {"private-access-token"}
