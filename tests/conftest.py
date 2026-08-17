"""Shared fixtures.

Every test runs against a throwaway SQLite file and the demo provider, so the
suite never needs ESPN credentials or a network connection.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point config and the database at a per-test temp directory."""
    # Ignore any real `.env` on the developer's machine entirely.
    monkeypatch.setenv("FWR_ENV_FILE", "")
    monkeypatch.setenv("FWR_DEMO_MODE", "true")
    monkeypatch.setenv("FWR_ESPN_SEASON", "2026")
    monkeypatch.setenv("FWR_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    # Clear both the prefixed and the bare ESPN names, so a developer's real
    # credentials in the shell can never leak into a test run.
    for name in (
        "FWR_ESPN_LEAGUE_ID", "FWR_ESPN_SWID", "FWR_ESPN_S2", "FWR_MY_DRAFT_SLOT",
        "ESPN_LEAGUE_ID", "ESPN_SEASON", "ESPN_YEAR", "ESPN_SWID", "SWID",
        "ESPN_S2", "ESPN_COOKIE_S2",
    ):
        monkeypatch.delenv(name, raising=False)

    from app import config, db
    from app.services import board

    config.reset_settings_cache()
    db.reset_engine()
    board.clear_cache()
    yield
    config.reset_settings_cache()
    db.reset_engine()
    board.clear_cache()


@pytest.fixture
def session():
    from app.db import init_db, session_scope

    init_db()
    with session_scope() as db_session:
        yield db_session


@pytest.fixture
def imported_league(session):
    """A fully imported demo league (settings, teams, players, projections)."""
    from app.services.importer import import_league

    return import_league(session)


@pytest.fixture
def engine(session, imported_league):
    from app.services.board import build_engine

    return build_engine(session, imported_league)


@pytest.fixture
def client(tmp_path):
    """FastAPI TestClient with the database initialised."""
    from fastapi.testclient import TestClient

    from app.db import init_db
    from app.main import app

    init_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def demo_shape():
    from app.engine.league_shape import LeagueShape

    return LeagueShape.from_slots(
        team_count=12,
        roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1},
        bench_slots=6,
        ir_slots=1,
    )


# ---------------------------------------------------------------------------
# Realistic ESPN payload fragments, used to test parsing without the network.
# ---------------------------------------------------------------------------


@pytest.fixture
def espn_settings_payload() -> dict:
    return {
        "name": "Test League",
        "size": 10,
        "rosterSettings": {
            "lineupSlotCounts": {
                "0": 1,   # QB
                "2": 2,   # RB
                "4": 2,   # WR
                "6": 1,   # TE
                "23": 1,  # FLEX
                "16": 1,  # D/ST
                "17": 1,  # K
                "20": 7,  # Bench
                "21": 1,  # IR
                "3": 0,   # RB/WR, unused
            }
        },
        "scoringSettings": {
            "scoringType": "H2H_POINTS",
            "matchupTieRule": "NONE",
            "playoffMatchupTieRule": "NONE",
            "scoringItems": [
                {"statId": 3, "points": 0.04, "pointsOverrides": {}},
                {"statId": 4, "points": 4.0, "pointsOverrides": {}},
                {"statId": 20, "points": -2.0, "pointsOverrides": {}},
                {"statId": 24, "points": 0.1, "pointsOverrides": {}},
                {"statId": 25, "points": 6.0, "pointsOverrides": {}},
                {"statId": 42, "points": 0.1, "pointsOverrides": {}},
                {"statId": 43, "points": 6.0, "pointsOverrides": {}},
                {"statId": 53, "points": 1.0, "pointsOverrides": {}},
                {"statId": 72, "points": -2.0, "pointsOverrides": {}},
                {"statId": 0, "points": 0.0, "pointsOverrides": {}},
            ],
        },
        "draftSettings": {
            "type": "SNAKE",
            "pickOrder": [4, 1, 7, 2, 9, 3, 10, 5, 8, 6],
            "keeperCount": 2,
            "timePerSelection": 90,
            "date": 1756000000000,
        },
        "acquisitionSettings": {
            "isUsingAcquisitionBudget": True,
            "acquisitionBudget": 100,
            "waiverProcessDays": ["WED"],
            "waiverHours": 3,
            "acquisitionLimit": -1,
        },
        "scheduleSettings": {
            "matchupPeriodCount": 14,
            "playoffTeamCount": 6,
            "playoffMatchupPeriodLength": 2,
            "playoffSeedingRule": "TOTAL_POINTS_SCORED",
            "divisions": [],
        },
        "tradeSettings": {"vetoVotesRequired": 4, "deadlineDate": 1765000000000},
    }


@pytest.fixture
def espn_player_entry() -> dict:
    """One entry as returned by the `kona_player_info` view."""
    return {
        "id": 4362628,
        "onTeamId": 0,
        "player": {
            "id": 4362628,
            "fullName": "Test Runningback",
            "defaultPositionId": 2,
            "proTeamId": 12,
            "eligibleSlots": [2, 3, 23, 7, 20, 21],
            "injuryStatus": "ACTIVE",
            "injured": False,
            "ownership": {
                "averageDraftPosition": 6.4,
                "percentOwned": 99.8,
                "percentStarted": 98.1,
            },
            "positionalRanking": 4,
            "draftRanksByRankType": {
                "STANDARD": {"rank": 7, "auctionValue": 55},
                "PPR": {"rank": 5, "auctionValue": 58},
            },
            "stats": [
                {
                    "seasonId": 2026,
                    "statSourceId": 1,
                    "statSplitTypeId": 0,
                    "scoringPeriodId": 0,
                    "appliedTotal": 272.4,
                    "appliedAverage": 16.02,
                    "stats": {"23": 265.0, "24": 1180.0, "25": 9.0, "53": 52.0,
                              "42": 410.0, "43": 2.0, "72": 2.0},
                },
                {
                    # A weekly projection that must be ignored.
                    "seasonId": 2026,
                    "statSourceId": 1,
                    "statSplitTypeId": 1,
                    "scoringPeriodId": 3,
                    "appliedTotal": 16.1,
                    "stats": {"24": 70.0},
                },
                {
                    # Actual (not projected) stats must be ignored.
                    "seasonId": 2026,
                    "statSourceId": 0,
                    "statSplitTypeId": 0,
                    "scoringPeriodId": 0,
                    "appliedTotal": 0.0,
                    "stats": {},
                },
            ],
        },
    }


@pytest.fixture
def drafted_league(client):
    """An imported league where every team holds a roster and one is mine.

    The demo league is deliberately pre-draft, so rosters are dealt out here to
    exercise everything that only exists after a draft. Players are distributed
    round-robin, which gives teams of genuinely different quality -- what a
    ranking needs in order to be worth testing.
    """
    client.post("/api/league/import")

    from app.db import session_scope
    from app.models import League, Player

    with session_scope() as session:
        league = session.query(League).one()
        players = (
            session.query(Player)
            .filter(Player.season == league.season)
            .order_by(Player.espn_rank)
            .limit(len(league.teams) * 14)
            .all()
        )
        teams = sorted(league.teams, key=lambda t: t.espn_team_id)
        for index, player in enumerate(players):
            team = teams[index % len(teams)]
            roster = list(team.roster or [])
            roster.append({"espn_player_id": player.espn_player_id, "slot": "BE"})
            team.roster = roster
        teams[0].is_mine = True
        session.commit()

    return client
