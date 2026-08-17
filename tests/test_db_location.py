"""The database must not move when the working directory does.

A service launched from somewhere other than the install directory used to
open a different (empty) SQLite file, which looked exactly like a fully
configured app reporting "no league configured yet".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def relative_db(tmp_path, monkeypatch):
    """Configure a relative sqlite URL, the way the default does."""
    from app import config, db

    monkeypatch.setenv("FWR_DATABASE_URL", "sqlite:///./data/relocation_test.db")
    config.reset_settings_cache()
    db.reset_engine()
    yield
    config.reset_settings_cache()
    db.reset_engine()


class TestDatabaseLocation:
    def test_a_relative_url_resolves_to_an_absolute_path(self, relative_db):
        from app.config import get_settings

        assert get_settings().sqlite_path().is_absolute()

    def test_the_engine_url_is_absolute_not_relative(self, relative_db):
        """The engine, not just the helper, must use the resolved path."""
        from app.db import get_engine

        url = str(get_engine().url)
        assert "./data" not in url
        assert "relocation_test.db" in url
        database = get_engine().url.database
        assert database is not None
        assert Path(database).is_absolute()

    def test_the_same_file_is_used_from_any_working_directory(
        self, relative_db, tmp_path, monkeypatch
    ):
        """This is the actual regression: a service starts in System32."""
        from app import db
        from app.config import get_settings

        expected = get_settings().sqlite_path()

        elsewhere = tmp_path / "somewhere-else"
        elsewhere.mkdir()
        original = os.getcwd()
        try:
            os.chdir(elsewhere)
            db.reset_engine()
            resolved = Path(db.get_engine().url.database)
        finally:
            os.chdir(original)

        assert resolved == expected
        # And nothing was created under the unrelated working directory.
        assert not (elsewhere / "data").exists()

    def test_an_absolute_url_is_left_alone(self, tmp_path, monkeypatch):
        from app import config, db

        target = tmp_path / "explicit.db"
        monkeypatch.setenv("FWR_DATABASE_URL", f"sqlite:///{target.as_posix()}")
        config.reset_settings_cache()
        db.reset_engine()
        try:
            assert Path(db.get_engine().url.database) == target
        finally:
            config.reset_settings_cache()
            db.reset_engine()
