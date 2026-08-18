"""Both sides of a trade need a roster.

Reported from real use: picking a trade partner showed none of their players.
The roster endpoint only ever returned my own team, and the screen filtered it
by the other team's player ids -- a set that could never intersect, so the
picker was empty by construction.
"""

from __future__ import annotations


class TestRosterByTeam:
    def _teams(self, drafted_league) -> list[dict]:
        return drafted_league.get("/api/season/roster").json()["teams"]

    def test_omitting_a_team_returns_my_own(self, drafted_league):
        body = drafted_league.get("/api/season/roster").json()
        assert body["team_id"] is None
        assert body["players"]

    def test_another_team_returns_that_team(self, drafted_league):
        others = [t for t in self._teams(drafted_league) if not t["is_mine"]]
        assert others, "fixture should have opponents"
        target = others[0]
        body = drafted_league.get(
            f"/api/season/roster?team_id={target['espn_team_id']}"
        ).json()
        assert body["team_id"] == target["espn_team_id"]
        assert body["team_name"] == target["name"]
        assert body["players"], "the trade partner's picker was empty"

    def test_their_roster_is_not_my_roster(self, drafted_league):
        """The actual regression: the two must be different sets of players."""
        mine = {p["espn_player_id"] for p in
                drafted_league.get("/api/season/roster").json()["players"]}
        others = [t for t in self._teams(drafted_league) if not t["is_mine"]]
        theirs = {
            p["espn_player_id"]
            for p in drafted_league.get(
                f"/api/season/roster?team_id={others[0]['espn_team_id']}"
            ).json()["players"]
        }
        assert theirs
        assert not (mine & theirs)

    def test_every_team_can_be_fetched(self, drafted_league):
        for team in self._teams(drafted_league):
            body = drafted_league.get(
                f"/api/season/roster?team_id={team['espn_team_id']}"
            ).json()
            assert body["players"], f"{team['name']} came back empty"

    def test_an_unknown_team_is_a_clear_404(self, drafted_league):
        response = drafted_league.get("/api/season/roster?team_id=9999")
        assert response.status_code == 404
        assert "9999" in response.json()["detail"]

    def test_players_carry_the_numbers_the_picker_shows(self, drafted_league):
        others = [t for t in self._teams(drafted_league) if not t["is_mine"]]
        player = drafted_league.get(
            f"/api/season/roster?team_id={others[0]['espn_team_id']}"
        ).json()["players"][0]
        for field in ("name", "position", "week_points", "season_points", "vor"):
            assert field in player
