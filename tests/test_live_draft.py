"""Phase 5 -- live draft state updates, through the API."""

from __future__ import annotations

import pytest


@pytest.fixture
def drafting(client):
    """A client with the demo league imported and a draft session open."""
    assert client.post("/api/league/import", json={}).status_code == 201
    return client


def board_top(client, position=None, limit=1):
    params = {"limit": limit}
    if position:
        params["position"] = position
    return client.get("/api/players", params=params).json()["players"]


class TestDraftSession:
    def test_a_session_is_created_on_demand(self, drafting):
        state = drafting.get("/api/draft").json()
        assert state["picks_made"] == 0
        assert state["team_count"] == 12
        assert state["rounds"] == 15
        assert state["draft_type"] == "SNAKE"

    def test_my_slot_can_be_changed(self, drafting):
        response = drafting.post("/api/draft/slot", json={"my_draft_slot": 7})
        assert response.status_code == 200
        assert response.json()["draft"]["my_draft_slot"] == 7

    def test_slot_change_reassigns_ownership_of_existing_picks(self, drafting):
        player = board_top(drafting)[0]
        drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"],
                                               "overall_pick": 7})
        drafting.post("/api/draft/slot", json={"my_draft_slot": 7})
        pick = drafting.get("/api/draft").json()["picks"][0]
        assert pick["is_mine"] is True


class TestRecordingPicks:
    def test_a_pick_is_recorded_with_derived_round_and_slot(self, drafting):
        player = board_top(drafting)[0]
        response = drafting.post(
            "/api/draft/pick", json={"espn_player_id": player["espn_player_id"]}
        )
        assert response.status_code == 201
        pick = response.json()["pick"]
        assert pick["overall_pick"] == 1
        assert pick["round"] == 1
        assert pick["draft_slot"] == 1
        assert pick["player_name"] == player["name"]
        assert pick["source"] == "manual"

    def test_picks_auto_advance(self, drafting):
        for expected in (1, 2, 3):
            player = board_top(drafting)[0]
            pick = drafting.post(
                "/api/draft/pick", json={"espn_player_id": player["espn_player_id"]}
            ).json()["pick"]
            assert pick["overall_pick"] == expected

    def test_snake_ownership_reverses_in_round_two(self, drafting):
        state = drafting.get("/api/draft").json()
        teams = state["team_count"]
        for _ in range(teams + 1):
            player = board_top(drafting)[0]
            drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        picks = {p["overall_pick"]: p["draft_slot"] for p in drafting.get("/api/draft").json()["picks"]}
        assert picks[teams] == teams
        assert picks[teams + 1] == teams  # snake turn

    def test_drafted_players_leave_the_board(self, drafting):
        player = board_top(drafting)[0]
        before = drafting.get("/api/players", params={"limit": 1}).json()["total"]
        drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        after = drafting.get("/api/players", params={"limit": 1}).json()
        assert after["total"] == before - 1
        assert all(p["espn_player_id"] != player["espn_player_id"] for p in after["players"])

    def test_a_player_cannot_be_drafted_twice(self, drafting):
        player = board_top(drafting)[0]
        drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        again = drafting.post(
            "/api/draft/pick", json={"espn_player_id": player["espn_player_id"]}
        )
        assert again.status_code == 400
        assert "already been drafted" in again.json()["detail"]

    def test_a_pick_slot_cannot_be_reused(self, drafting):
        players = board_top(drafting, limit=2)
        drafting.post("/api/draft/pick",
                      json={"espn_player_id": players[0]["espn_player_id"], "overall_pick": 5})
        clash = drafting.post(
            "/api/draft/pick",
            json={"espn_player_id": players[1]["espn_player_id"], "overall_pick": 5},
        )
        assert clash.status_code == 400

    def test_unknown_player_is_rejected(self, drafting):
        response = drafting.post("/api/draft/pick", json={"espn_player_id": 987654321})
        assert response.status_code == 400
        assert "Unknown player" in response.json()["detail"]

    def test_pick_beyond_the_draft_is_rejected(self, drafting):
        player = board_top(drafting)[0]
        response = drafting.post(
            "/api/draft/pick",
            json={"espn_player_id": player["espn_player_id"], "overall_pick": 9999},
        )
        assert response.status_code == 400


class TestUndoAndReset:
    def test_undo_removes_the_last_pick(self, drafting):
        player = board_top(drafting)[0]
        drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        response = drafting.post("/api/draft/undo")
        assert response.status_code == 200
        assert response.json()["draft"]["picks_made"] == 0

    def test_undo_puts_the_player_back_on_the_board(self, drafting):
        player = board_top(drafting)[0]
        drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        drafting.post("/api/draft/undo")
        assert board_top(drafting)[0]["espn_player_id"] == player["espn_player_id"]

    def test_undo_with_no_picks_is_rejected(self, drafting):
        assert drafting.post("/api/draft/undo").status_code == 400

    def test_delete_a_specific_pick(self, drafting):
        player = board_top(drafting)[0]
        drafting.post("/api/draft/pick",
                      json={"espn_player_id": player["espn_player_id"], "overall_pick": 3})
        assert drafting.delete("/api/draft/pick/3").status_code == 200
        assert drafting.delete("/api/draft/pick/3").status_code == 404

    def test_reset_clears_everything(self, drafting):
        for _ in range(4):
            player = board_top(drafting)[0]
            drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        response = drafting.post("/api/draft/reset")
        assert response.json()["draft"]["picks_made"] == 0


class TestStateRecalculation:
    def test_the_clock_advances_with_each_pick(self, drafting):
        before = drafting.get("/api/draft/state").json()["draft_position"]
        player = board_top(drafting)[0]
        drafting.post("/api/draft/pick", json={"espn_player_id": player["espn_player_id"]})
        after = drafting.get("/api/draft/state").json()["draft_position"]
        assert after["current_pick"] == before["current_pick"] + 1
        assert after["on_the_clock_slot"] != before["on_the_clock_slot"]

    def test_recommendations_change_when_the_top_player_is_taken(self, drafting):
        first = drafting.get("/api/draft/recommendations?limit=1").json()["best_pick"]
        drafting.post("/api/draft/pick", json={"espn_player_id": first["espn_player_id"]})
        second = drafting.get("/api/draft/recommendations?limit=1").json()["best_pick"]
        assert second["espn_player_id"] != first["espn_player_id"]

    def test_my_roster_grows_only_from_my_slots(self, drafting):
        # Slot 1 owns picks 1 and 24.
        for pick in (1, 2, 3):
            player = board_top(drafting)[0]
            drafting.post("/api/draft/pick",
                          json={"espn_player_id": player["espn_player_id"], "overall_pick": pick})
        team = drafting.get("/api/team").json()
        assert team["picks_made"] == 1

    def test_needs_update_as_i_draft(self, drafting):
        before = drafting.get("/api/team").json()["remaining_needs"]
        before_rb = next(n for n in before if n["position"] == "RB")
        assert before_rb["starters_filled"] == 0

        # Take the highest-projecting RB, which is what moves the marginal
        # value -- the top RB by Draft Score isn't necessarily that player.
        candidates = drafting.get(
            "/api/players", params={"position": "RB", "limit": 20}
        ).json()["players"]
        rb = max(candidates, key=lambda p: p["projected_points"])
        drafting.post("/api/draft/pick",
                      json={"espn_player_id": rb["espn_player_id"], "overall_pick": 1})

        after = drafting.get("/api/team").json()["remaining_needs"]
        after_rb = next(n for n in after if n["position"] == "RB")
        assert after_rb["starters_filled"] == 1
        assert after_rb["marginal_value"] < before_rb["marginal_value"]

    def test_availability_tightens_as_our_next_pick_approaches(self, drafting):
        """The same player is likelier to survive a 2-pick gap than a 22-pick gap."""
        drafting.post("/api/draft/slot", json={"my_draft_slot": 1})
        far = drafting.get("/api/players", params={"limit": 40}).json()

        # Advance to pick 23; slot 1 picks again at 24, so the gap is now tiny.
        for pick in range(1, 23):
            player = drafting.get("/api/players", params={"limit": 1}).json()["players"][0]
            drafting.post("/api/draft/pick",
                          json={"espn_player_id": player["espn_player_id"], "overall_pick": pick})
        near = drafting.get("/api/players", params={"limit": 40}).json()

        assert far["draft_position"]["picks_until_my_next"] > (
            near["draft_position"]["picks_until_my_next"]
        )
        near_by_id = {p["espn_player_id"]: p for p in near["players"]}
        overlap = [p for p in far["players"] if p["espn_player_id"] in near_by_id]
        assert overlap, "expected some players to survive 22 picks"
        assert any(
            near_by_id[p["espn_player_id"]]["availability_next_pick"] >= p["availability_next_pick"]
            for p in overlap
        )

    def test_market_drift_is_reported(self, drafting):
        for pick in range(1, 16):
            player = drafting.get("/api/players", params={"limit": 1}).json()["players"][0]
            drafting.post("/api/draft/pick",
                          json={"espn_player_id": player["espn_player_id"], "overall_pick": pick})
        meta = drafting.get("/api/draft/state").json()["meta"]
        assert "market_drift" in meta
