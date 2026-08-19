"""The wire has to say why it is offering what it is offering.

Reported as "waivers only ever shows defenses". It was arithmetically right --
a free agent only appears if he improves the starting lineup, and on a full
roster with a weak defence the only position the wire wins is DST. But a screen
that lists eight defences and silently discards 172 other candidates is
indistinguishable from a broken one, which is how it was read for weeks.
"""

from __future__ import annotations

import pytest

from app.engine.league_shape import LeagueShape
from app.engine.waivers import explain_by_position, recommend_waivers
from app.engine.weekly import WeeklyPlayer

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1}


@pytest.fixture
def shape() -> LeagueShape:
    return LeagueShape.from_slots(team_count=12, roster_slots=SLOTS, bench_slots=6)


def wp(player_id: int, name: str, position: str, points: float, vor: float = 0.0):
    return WeeklyPlayer(
        espn_player_id=player_id,
        name=name,
        position=position,
        pro_team="FA",
        week_points=round(points / 17, 2),
        season_points=points,
        vor=vor,
        availability="FREEAGENT",
    )


@pytest.fixture
def roster():
    """Strong everywhere except defence -- the shape that produced the report."""
    return [
        wp(1, "My QB", "QB", 444, 40), wp(2, "My RB1", "RB", 490, 218),
        wp(3, "My RB2", "RB", 419, 146), wp(4, "My WR1", "WR", 282, 5),
        wp(5, "My WR2", "WR", 280, 3), wp(6, "My TE", "TE", 297, 99),
        wp(7, "My K", "K", 157, 16), wp(8, "My DST", "DST", 165, -17),
        wp(9, "My Flex", "RB", 263, -10), wp(10, "Bench RB", "RB", 106, -167),
    ]


@pytest.fixture
def wire():
    """Only the defences beat what this roster already starts."""
    return [
        wp(101, "Wire QB", "QB", 380), wp(102, "Wire RB", "RB", 240),
        wp(103, "Wire WR", "WR", 205), wp(104, "Wire TE", "TE", 180),
        wp(105, "Wire K", "K", 150), wp(106, "Better DST", "DST", 190),
        wp(107, "Good DST", "DST", 182),
    ]


class TestTheWireStillOnlyRecommendsWhatHelps:
    def test_only_the_defences_are_recommended(self, roster, wire, shape):
        targets = recommend_waivers(
            roster=roster, free_agents=wire, shape=shape, week=1,
            faab_budget=100, roster_is_full=True,
        )
        assert {t.player.position for t in targets} == {"DST"}


class TestEveryPositionIsAccountedFor:
    def test_every_position_on_the_wire_gets_a_verdict(self, roster, wire, shape):
        targets = recommend_waivers(
            roster=roster, free_agents=wire, shape=shape, week=1,
            faab_budget=100, roster_is_full=True,
        )
        verdicts = explain_by_position(
            roster, wire, shape, {t.player.espn_player_id for t in targets}
        )
        assert {v.position for v in verdicts} == {"QB", "RB", "WR", "TE", "K", "DST"}

    def test_a_rejected_position_names_the_player_who_blocked_it(
        self, roster, wire, shape
    ):
        verdicts = explain_by_position(roster, wire, shape, set())
        wr = next(v for v in verdicts if v.position == "WR")
        assert not wr.helps
        assert wr.best_name == "Wire WR"
        assert "My WR2" in wr.note, wr.note

    def test_the_position_that_helps_is_marked(self, roster, wire, shape):
        targets = recommend_waivers(
            roster=roster, free_agents=wire, shape=shape, week=1,
            faab_budget=100, roster_is_full=True,
        )
        verdicts = explain_by_position(
            roster, wire, shape, {t.player.espn_player_id for t in targets}
        )
        assert next(v for v in verdicts if v.position == "DST").helps
        assert not next(v for v in verdicts if v.position == "QB").helps


class TestPositionsAreNotCrowdedOut:
    def test_the_route_looks_at_each_position_separately(self, drafted_league):
        """A flat top-N by points starves kickers and defences off the wire.

        Quarterbacks project three times what a kicker does, so one cut across
        every position drops K and DST first -- exactly the positions where the
        wire most often wins.
        """
        response = drafted_league.get("/api/season/waivers")
        assert response.status_code == 200, response.text
        body = response.json()

        positions = {row["position"] for row in body["by_position"]}
        assert {"K", "DST"} <= positions, (
            f"kickers and defences never reached the wire: {sorted(positions)}"
        )
        assert body["free_agents_available"] >= body["free_agents_considered"]
