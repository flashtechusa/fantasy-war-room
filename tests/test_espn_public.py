"""ESPN's public projections -- the source that makes a Yahoo league usable.

Yahoo publishes no projections at all, so a Yahoo league needs a source that
needs no credentials of its own. ESPN serves projections for a default league,
which is exactly that.

The network call is not exercised here (no network in the suite); the parser is,
because it is the part that can be wrong while everything still looks fine.
Storing what it returns is wired into the projection service separately, and is
covered there.
"""

from __future__ import annotations

from app.projections.espn_public import PublicProjection, parse_projections


def _entry(player_id: int, name: str, position_id: int, stats: list[dict]) -> dict:
    return {
        "id": player_id,
        "player": {
            "id": player_id,
            "fullName": name,
            "defaultPositionId": position_id,
            "proTeamId": 12,
            "eligibleSlots": [2, 23, 20],
            "stats": stats,
        },
    }


SEASON_PROJECTION = {
    "seasonId": 2026,
    "statSourceId": 1,
    "statSplitTypeId": 0,
    "scoringPeriodId": 0,
    "appliedTotal": 260.0,
    "appliedAverage": 16.25,
    "stats": {"24": 1100.0, "25": 8.0, "53": 45.0, "42": 380.0},
}


class TestParsing:
    def test_season_projection_is_taken(self):
        players = parse_projections(
            {"players": [_entry(1, "Test Back", 2, [SEASON_PROJECTION])]}, 2026
        )
        assert len(players) == 1
        assert players[0].name == "Test Back"
        assert players[0].position == "RB"
        assert players[0].raw_stats["24"] == 1100.0
        assert players[0].projected_games == 16.0

    def test_actuals_and_weekly_splits_are_ignored(self):
        entry = _entry(
            2,
            "Weekly Only",
            2,
            [
                {**SEASON_PROJECTION, "statSourceId": 0},          # actuals
                {**SEASON_PROJECTION, "statSplitTypeId": 1, "scoringPeriodId": 3},
            ],
        )
        assert parse_projections({"players": [entry]}, 2026) == []

    def test_another_season_is_ignored(self):
        entry = _entry(3, "Last Year", 2, [{**SEASON_PROJECTION, "seasonId": 2025}])
        assert parse_projections({"players": [entry]}, 2026) == []

    def test_a_payload_we_cannot_read_yields_nothing(self):
        assert parse_projections({"nothing": "useful"}, 2026) == []
