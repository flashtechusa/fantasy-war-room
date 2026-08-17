"""Every team scored the same way, so a number has something to mean against."""

from __future__ import annotations

import pytest


@pytest.fixture
def drafted_league(client):
    """An imported league where every team has a roster.

    The demo league is deliberately pre-draft, so rosters are dealt out here
    to exercise the populated path. Players are distributed round-robin, which
    gives teams of genuinely different quality -- exactly what a ranking needs
    to be worth testing.
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
        session.commit()

    return client


class TestBeforeAnyoneHasDrafted:
    def test_it_says_so_rather_than_returning_a_blank_table(self, client):
        client.post("/api/league/import")
        body = client.get("/api/team/league").json()
        assert body["teams"] == []
        assert "no team has a roster" in body["note"].lower()


class TestAllTeams:
    def _teams(self, drafted_league) -> dict:
        response = drafted_league.get("/api/team/league")
        assert response.status_code == 200
        return response.json()

    def test_it_returns_a_row_per_rostered_team(self, drafted_league):
        body = self._teams(drafted_league)
        assert body["team_count"] > 1
        assert len(body["teams"]) == body["team_count"]

    def test_every_team_is_scored_the_same_way(self, drafted_league):
        for team in self._teams(drafted_league)["teams"]:
            assert "roster_construction_score" in team
            assert "projected_starter_points" in team
            assert "positional_strength" in team

    def test_ranks_are_dense_and_start_at_one(self, drafted_league):
        ranks = sorted(t["rank"] for t in self._teams(drafted_league)["teams"])
        assert ranks == list(range(1, len(ranks) + 1))

    def test_rank_one_really_is_the_highest_projection(self, drafted_league):
        teams = self._teams(drafted_league)["teams"]
        best = max(teams, key=lambda t: t["projected_starter_points"])
        assert best["rank"] == 1

    def test_construction_is_ranked_separately_from_points(self, drafted_league):
        """Roster shape and roster strength are different questions."""
        teams = self._teams(drafted_league)["teams"]
        construction = sorted(t["construction_rank"] for t in teams)
        assert construction == list(range(1, len(teams) + 1))
        best_built = min(teams, key=lambda t: t["construction_rank"])
        assert best_built["roster_construction_score"] == max(
            t["roster_construction_score"] for t in teams
        )

    def test_the_median_gives_a_bare_score_a_reference_point(self, drafted_league):
        """Without this, 55.5/100 reads like a failing grade instead of average."""
        body = self._teams(drafted_league)
        scores = [t["roster_construction_score"] for t in body["teams"]]
        assert min(scores) <= body["league_median_score"] <= max(scores)

    def test_the_median_points_sit_inside_the_range_too(self, drafted_league):
        body = self._teams(drafted_league)
        points = [t["projected_starter_points"] for t in body["teams"]]
        assert min(points) <= body["league_median_points"] <= max(points)

    def test_at_most_one_team_is_mine(self, drafted_league):
        teams = self._teams(drafted_league)["teams"]
        assert sum(1 for t in teams if t["is_mine"]) <= 1

    def test_teams_are_named_so_the_table_is_readable(self, drafted_league):
        for team in self._teams(drafted_league)["teams"]:
            assert team["name"]
            assert team["espn_team_id"]

    def test_it_explains_what_the_ranking_means(self, drafted_league):
        assert "projected" in self._teams(drafted_league)["note"].lower()
