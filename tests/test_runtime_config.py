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


class TestSelfUpdate:
    """Shipping a fix must not require a terminal."""

    def test_version_reports_the_deployed_commit(self, client):
        body = client.get("/api/system/version").json()
        assert "git" in body
        if body["git"]:
            assert body["commit"]
            assert body["branch"]

    def test_update_is_reachable_and_reports_its_outcome(self, client, monkeypatch):
        import app.api.routes_system as system

        calls: list[tuple] = []

        def fake_git(*args):
            calls.append(args)
            if args[0] == "rev-parse" and args[1] == "HEAD":
                return (0, "abc123" if len(calls) < 3 else "def456")
            if args[0] == "pull":
                return (0, "Updating abc123..def456")
            if args[0] == "log":
                return (0, "def456 a newer commit")
            return (0, "main")

        monkeypatch.setattr(system, "_git", fake_git)
        body = client.post("/api/system/update").json()
        assert body["updated"] is True
        assert "restarts itself" in body["detail"]
        # Only ever a fast-forward pull of the current branch.
        pulls = [c for c in calls if c[0] == "pull"]
        assert pulls and all("--ff-only" in c for c in pulls)

    def test_a_blocked_pull_explains_why(self, client, monkeypatch):
        import app.api.routes_system as system

        def fake_git(*args):
            if args[0] == "pull":
                return (1, "error: Your local changes would be overwritten")
            return (0, "main")

        monkeypatch.setattr(system, "_git", fake_git)
        response = client.post("/api/system/update")
        assert response.status_code == 409
        assert "local edits" in response.json()["detail"]

    def test_no_change_is_reported_as_already_current(self, client, monkeypatch):
        import app.api.routes_system as system

        monkeypatch.setattr(
            system, "_git",
            lambda *args: (0, "same") if args[0] == "rev-parse" else (0, "ok"),
        )
        body = client.post("/api/system/update").json()
        assert body["updated"] is False
        assert "latest" in body["detail"]
