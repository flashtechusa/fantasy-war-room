"""Runtime ESPN configuration entered through the UI."""

from __future__ import annotations


class TestConfigEndpoint:
    def test_starts_unconfigured(self, client):
        body = client.get("/api/config").json()
        assert body["espn_league_id"] is None
        assert body["swid_set"] is False
        assert body["sources"]["espn_league_id"] == "environment"

    def test_saving_config_takes_effect_immediately(self, client):
        response = client.put(
            "/api/config",
            json={"espn_league_id": 11507, "espn_season": 2026, "demo_mode": False},
        )
        assert response.status_code == 200
        config = response.json()["config"]
        assert config["espn_league_id"] == 11507
        assert config["sources"]["espn_league_id"] == "ui"
        # And the rest of the app sees it without a restart.
        assert client.get("/api/health").json()["espn"]["league_id_configured"] is True

    def test_swid_braces_are_normalised_on_save(self, client):
        client.put("/api/config", json={"espn_swid": "AAAA-BBBB", "espn_s2": "cookie"})
        assert client.get("/api/config").json()["has_private_credentials"] is True

    def test_secrets_are_never_returned(self, client):
        secret = "super-secret-cookie-value"
        response = client.put(
            "/api/config",
            json={"espn_league_id": 11507, "espn_swid": "{ABC}", "espn_s2": secret},
        )
        assert secret not in response.text
        assert "{ABC}" not in response.text
        assert secret not in client.get("/api/config").text
        assert client.get("/api/config").json()["espn_s2_set"] is True

    def test_partial_updates_keep_existing_values(self, client):
        client.put("/api/config", json={"espn_league_id": 11507, "espn_s2": "cookie"})
        client.put("/api/config", json={"espn_season": 2027})
        config = client.get("/api/config").json()
        assert config["espn_league_id"] == 11507
        assert config["espn_season"] == 2027
        assert config["espn_s2_set"] is True

    def test_reset_falls_back_to_the_environment(self, client):
        client.put("/api/config", json={"espn_league_id": 11507})
        client.delete("/api/config")
        config = client.get("/api/config").json()
        assert config["espn_league_id"] is None
        assert config["sources"]["espn_league_id"] == "environment"

    def test_invalid_values_are_rejected(self, client):
        assert client.put("/api/config", json={"espn_league_id": 0}).status_code == 422
        assert client.put("/api/config", json={"espn_season": 1800}).status_code == 422
        assert client.put("/api/config", json={"my_draft_slot": 99}).status_code == 422

    def test_configuring_a_league_switches_off_the_demo_provider(self, client):
        """Demo mode must not silently override a real league mid-draft."""
        assert client.get("/api/health").json()["demo_mode"] is True
        client.put("/api/config", json={"espn_league_id": 11507, "demo_mode": False})
        assert client.get("/api/health").json()["demo_mode"] is False
