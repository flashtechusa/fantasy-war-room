"""Which build is serving you, answerable without a terminal.

The VPS installs from a zip, so /api/system/version -- which shells out to git
-- cannot answer there. For days the only way to tell whether an update had
landed was to compare numbers on a screen and guess, and the guess was wrong:
the auto-updater was rolling every new version back while leaving the newer
scripts on disk, so the app and the scripts beside it were different versions.
"""

from __future__ import annotations


class TestHealthReportsTheBuild:
    def test_health_names_the_served_bundle(self, anon_client):
        """Anonymous on purpose -- you check this before signing in."""
        body = anon_client.get("/api/health").json()
        assert "build" in body
        bundle = body["build"]["bundle"]
        assert bundle.startswith("index-") and bundle.endswith(".js"), bundle

    def test_the_fingerprint_matches_what_is_actually_served(self, anon_client):
        from pathlib import Path

        import app.main as main_module

        index = (
            Path(main_module.__file__).resolve().parent / "static" / "index.html"
        ).read_text(encoding="utf-8")
        assert anon_client.get("/api/health").json()["build"]["bundle"] in index

    def test_a_missing_stamp_file_is_not_an_error(self, anon_client):
        """The commit stamp only exists once the auto-updater has run."""
        assert anon_client.get("/api/health").status_code == 200
        assert "commit" in anon_client.get("/api/health").json()["build"]


class TestHealthStaysAnonymousSafe:
    def test_a_signed_out_caller_is_told_nothing_about_the_league(self, anon_client):
        """Why the updater must not read league_imported from here.

        This endpoint answers an unauthenticated caller, and an unauthenticated
        caller has no league. That is correct, and it is exactly what made
        `league_imported` a useless signal for a deployment script.
        """
        body = anon_client.get("/api/health").json()
        assert body["league_imported"] is False
        assert body["league"] is None
