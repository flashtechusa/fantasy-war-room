"""The simulator's numbers need a real reference class to mean anything.

Reported as nonsense from real use: the panel called 2890 a "bad draft" while
every actual team in the league scored below it. The figure was right; the
label implied a human benchmark it never had.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def drafted_league(client):
    """An imported league where every team holds a roster."""
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
        session.commit()

    return client


def _simulate(client) -> dict:
    response = client.post("/api/simulate", json={"simulations": 25, "seed": 7})
    assert response.status_code == 200
    return response.json()


class TestActualLeagueContext:
    def test_the_real_league_is_reported_alongside(self, drafted_league):
        actual = _simulate(drafted_league)["actual_league"]
        assert actual["available"] is True
        assert actual["team_count"] > 1

    def test_best_median_and_worst_are_ordered(self, drafted_league):
        actual = _simulate(drafted_league)["actual_league"]
        assert actual["worst"] <= actual["median"] <= actual["best"]

    def test_it_is_measured_the_same_way_as_the_teams_screen(self, drafted_league):
        """Both must use the same lineup maths, or comparing them is a lie."""
        actual = _simulate(drafted_league)["actual_league"]
        teams = drafted_league.get("/api/team/league").json()["teams"]
        assert actual["best"] == pytest.approx(
            max(t["projected_starter_points"] for t in teams), abs=0.05
        )
        assert actual["worst"] == pytest.approx(
            min(t["projected_starter_points"] for t in teams), abs=0.05
        )

    def test_the_simulated_figures_are_still_present(self, drafted_league):
        expected = _simulate(drafted_league)["expected_roster_points"]
        assert expected["p10"] <= expected["mean"] <= expected["p90"]

    def test_it_says_so_when_no_league_has_drafted(self, client):
        """Pre-draft there is no reference class, and it must not invent one."""
        client.post("/api/league/import")
        assert _simulate(client)["actual_league"]["available"] is False
