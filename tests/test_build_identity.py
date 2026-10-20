"""The header has to be able to say which code is running -- all of it.

The bundle fingerprint only changes when the FRONTEND does. A backend-only
deploy -- a scoring fix, a waiver rule, a migration -- leaves it byte-identical,
so there is no way to tell from the app whether the fix landed. That happened: a
user watched an unchanged fingerprint and reasonably concluded a waiver fix had
not shipped, when the question could not be answered that way at all.

So the health payload carries the applied commit as well, and the header shows
both.
"""

from __future__ import annotations


def test_health_reports_both_halves_of_the_build(client):
    build = client.get("/api/health").json().get("build")
    assert build is not None
    assert "bundle" in build, "which frontend"
    assert "commit" in build, "which backend"


def test_the_commit_comes_from_what_the_updater_applied(tmp_path, monkeypatch):
    """auto-update.ps1 writes the sha it installed next to the database."""
    import app.main as main

    stamp = tmp_path / "last-applied-commit.txt"
    stamp.write_text("b040001abcdef1234567890\n", encoding="utf-8")

    class _Settings:
        @staticmethod
        def sqlite_path():
            return tmp_path / "fantasy_war_room.db"

    monkeypatch.setattr(main, "get_settings", lambda: _Settings())
    # Eight characters, which is what the header shows verbatim.
    assert main._build_fingerprint()["commit"] == "b040001a"      # noqa: SLF001


def test_a_missing_stamp_is_not_an_error(tmp_path, monkeypatch):
    # A developer checkout has no stamp file; the header just says less.
    import app.main as main

    class _Settings:
        @staticmethod
        def sqlite_path():
            return tmp_path / "nothing.db"

    monkeypatch.setattr(main, "get_settings", lambda: _Settings())
    assert main._build_fingerprint()["commit"] == ""      # noqa: SLF001
