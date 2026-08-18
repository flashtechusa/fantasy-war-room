"""Choosing between ESPN and Yahoo.

The platform switch is the one place where a wrong answer is silent: pointed at
the wrong platform, the app imports *something* and looks fine. These cover the
selection itself, the messages a half-configured Yahoo install gets, and the
promise that credentials entered in the UI are never handed back out.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.services.importer import configured_league_id
from app.services.provider import DemoProvider, EspnProvider, YahooProvider, build_provider
from app.yahoo.client import YahooConnectionError


def _settings(**overrides) -> Settings:
    base = {
        "demo_mode": False,
        "espn_league_id": None,
        "platform": "espn",
        "espn_season": 2026,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestProviderSelection:
    def test_demo_mode_wins_over_everything(self):
        settings = _settings(demo_mode=True, platform="yahoo", yahoo_league_id=1)
        assert isinstance(build_provider(settings), DemoProvider)

    def test_espn_is_the_default(self):
        assert isinstance(build_provider(_settings(espn_league_id=99)), EspnProvider)

    def test_yahoo_needs_an_app_before_it_can_be_built(self):
        settings = _settings(platform="yahoo", yahoo_league_id=123)
        with pytest.raises(YahooConnectionError, match="developer.yahoo.com"):
            build_provider(settings)

    def test_yahoo_needs_a_league_id(self):
        settings = _settings(
            platform="yahoo", yahoo_client_id="a" * 12, yahoo_client_secret="b" * 12
        )
        with pytest.raises(YahooConnectionError, match="league id"):
            build_provider(settings)

    def test_yahoo_needs_the_handshake(self):
        settings = _settings(
            platform="yahoo",
            yahoo_league_id=123,
            yahoo_client_id="a" * 12,
            yahoo_client_secret="b" * 12,
        )
        with pytest.raises(YahooConnectionError, match="Connect Yahoo"):
            build_provider(settings)

    def test_a_fully_configured_yahoo_install_builds(self):
        settings = _settings(
            platform="yahoo",
            yahoo_league_id=123,
            yahoo_client_id="a" * 12,
            yahoo_client_secret="b" * 12,
            yahoo_access_token="token",
            yahoo_refresh_token="refresh",
        )
        provider = build_provider(settings)
        assert isinstance(provider, YahooProvider)
        assert provider.source == "yahoo"

    def test_the_league_id_follows_the_platform(self):
        espn = _settings(espn_league_id=11, yahoo_league_id=22)
        yahoo = _settings(platform="yahoo", espn_league_id=11, yahoo_league_id=22)
        assert configured_league_id(espn) == 11
        assert configured_league_id(yahoo) == 22

    def test_an_unknown_platform_falls_back_to_espn(self):
        assert _settings(platform="sleeper").platform == "espn"


class TestConfigSurface:
    def test_platform_and_yahoo_league_can_be_set_from_the_ui(self, client):
        response = client.put(
            "/api/config",
            json={"platform": "yahoo", "yahoo_league_id": 445566},
        )
        assert response.status_code == 200
        config = response.json()["config"]
        assert config["platform"] == "yahoo"
        assert config["yahoo_league_id"] == 445566

    def test_yahoo_secrets_are_reported_but_never_returned(self, client):
        client.put(
            "/api/config",
            json={
                "platform": "yahoo",
                "yahoo_client_id": "client-id-value",
                "yahoo_client_secret": "client-secret-value",
            },
        )
        body = client.get("/api/config").json()
        assert body["yahoo_app_configured"] is True
        assert "client-secret-value" not in str(body)

    def test_an_unknown_platform_is_rejected_rather_than_ignored(self, client):
        response = client.put("/api/config", json={"platform": "sleeper"})
        assert response.status_code == 422


class TestYahooRoutes:
    def test_status_says_what_is_missing(self, client):
        body = client.get("/api/yahoo/status").json()
        assert body["app_configured"] is False
        assert body["connected"] is False

    def test_starting_the_handshake_needs_an_app(self, client):
        response = client.post("/api/yahoo/auth/start")
        assert response.status_code == 400
        assert "developer.yahoo.com" in response.json()["detail"]

    def test_the_authorise_url_is_built_once_an_app_is_configured(self, client):
        client.put(
            "/api/yahoo/app",
            json={
                "yahoo_client_id": "client-id-value",
                "yahoo_client_secret": "client-secret-value",
                "yahoo_league_id": 445566,
            },
        )
        body = client.post("/api/yahoo/auth/start").json()
        assert body["authorize_url"].startswith("https://api.login.yahoo.com/oauth2/request_auth")
        assert body["out_of_band"] is True
        assert client.get("/api/config").json()["platform"] == "yahoo"

    def test_listing_leagues_needs_a_connection(self, client):
        assert client.get("/api/yahoo/leagues").status_code == 409

    def test_health_probe_explains_each_missing_step(self, client):
        client.put("/api/config", json={"platform": "yahoo", "demo_mode": False})
        body = client.get("/api/health/yahoo").json()
        assert body["connected"] is False
        assert "developer.yahoo.com" in body["detail"]

    def test_disconnecting_clears_the_tokens(self, client):
        from app.db import session_scope
        from app.services.yahoo_auth import store_tokens
        from app.yahoo.oauth import YahooTokens

        with session_scope() as session:
            store_tokens(session, YahooTokens("access", "refresh", 9_999_999_999.0, "GUID"))

        assert client.get("/api/config").json()["yahoo_connected"] is True
        assert client.delete("/api/yahoo/auth").status_code == 200
        assert client.get("/api/config").json()["yahoo_connected"] is False
