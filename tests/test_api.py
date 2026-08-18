"""End-to-end HTTP behaviour."""

from __future__ import annotations

import pytest


@pytest.fixture
def ready(client):
    assert client.post("/api/league/import", json={}).status_code == 201
    return client


class TestHealth:
    def test_health_before_import(self, client):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["league_imported"] is False
        assert body["players_loaded"] == 0

    def test_health_never_leaks_credentials(self, client):
        body = client.get("/api/health").json()
        text = str(body).lower()
        assert "swid" not in text
        assert "espn_s2" not in text
        # Pinned deliberately: a new key here has to be a conscious decision,
        # because this block is one `settings.espn_swid` away from a leak.
        assert set(body["espn"]) == {
            "league_id_configured",
            "private_credentials_configured",
            "reachable_config",
            "draft_source",
        }

    def test_espn_probe_reports_demo_mode(self, client):
        body = client.get("/api/health/espn").json()
        assert body["demo_mode"] is True
        assert body["connected"] is False


class TestBeforeImport:
    def test_league_endpoint_404s(self, client):
        response = client.get("/api/league")
        assert response.status_code == 404
        assert "import" in response.json()["detail"].lower()

    def test_board_404s(self, client):
        assert client.get("/api/players").status_code == 404


class TestLeagueEndpoints:
    def test_import_returns_the_parsed_league(self, client):
        body = client.post("/api/league/import", json={}).json()
        assert body["imported"] is True
        assert body["players_imported"] > 200
        assert body["league"]["roster"]["draft_rounds"] == 15

    def test_league_exposes_everything_needed_to_verify_the_rules(self, ready):
        body = ready.get("/api/league").json()
        for key in ("scoring", "roster", "draft", "waivers", "playoffs", "teams"):
            assert key in body
        assert body["scoring"]["rules"]
        assert body["roster"]["starting_slots"]
        assert len(body["teams"]) == 12
        assert body["teams"][0]["owners"]

    def test_history_is_available(self, ready):
        body = ready.get("/api/league/history").json()
        assert body["seasons"]
        picks = body["picks_by_season"][str(body["seasons"][0])]
        assert picks[0]["overall_pick"] == 1

    def test_projection_sources_are_listed(self, ready):
        keys = {source["key"] for source in ready.get("/api/league/projection-sources").json()["sources"]}
        assert {"espn", "demo"} <= keys

    def test_player_refresh(self, ready):
        assert ready.post("/api/league/players/refresh").json()["players_imported"] > 200


class TestBoardEndpoint:
    def test_default_board(self, ready):
        body = ready.get("/api/players").json()
        assert body["total"] > 200
        assert body["players"]
        assert body["draft_position"]["current_pick"] == 1
        assert body["meta"]["scoring_format"] == "0.5 PPR"

    @pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE", "K", "DST"])
    def test_position_filters(self, ready, position):
        body = ready.get("/api/players", params={"position": position}).json()
        assert body["players"]
        assert all(player["position"] == position for player in body["players"])

    def test_flex_filter_covers_rb_wr_te(self, ready):
        body = ready.get("/api/players", params={"position": "FLEX"}).json()
        positions = {player["position"] for player in body["players"]}
        assert positions <= {"RB", "WR", "TE"}
        assert len(positions) > 1

    def test_invalid_filter_is_rejected(self, ready):
        assert ready.get("/api/players", params={"position": "LB"}).status_code == 400

    def test_search_matches_names(self, ready):
        first = ready.get("/api/players", params={"limit": 1}).json()["players"][0]
        term = first["name"].split()[-1]
        body = ready.get("/api/players", params={"search": term}).json()
        assert body["players"]
        assert all(term.lower() in p["name"].lower() or term.lower() in p["pro_team"].lower()
                   for p in body["players"])

    def test_search_matches_nfl_team(self, ready):
        body = ready.get("/api/players", params={"search": "KC"}).json()
        assert all("kc" in (p["pro_team"] + p["name"]).lower() for p in body["players"])

    def test_pagination(self, ready):
        page1 = ready.get("/api/players", params={"limit": 10, "offset": 0}).json()
        page2 = ready.get("/api/players", params={"limit": 10, "offset": 10}).json()
        assert len(page1["players"]) == 10
        ids1 = {p["espn_player_id"] for p in page1["players"]}
        ids2 = {p["espn_player_id"] for p in page2["players"]}
        assert not (ids1 & ids2)

    def test_detail_flag_adds_reasoning(self, ready):
        plain = ready.get("/api/players", params={"limit": 1}).json()["players"][0]
        detailed = ready.get("/api/players", params={"limit": 1, "detail": True}).json()["players"][0]
        assert "reasons" not in plain
        assert detailed["reasons"] and detailed["components"]

    def test_board_columns_are_all_present(self, ready):
        player = ready.get("/api/players", params={"limit": 1}).json()["players"][0]
        for field in (
            "rank", "name", "position", "pro_team", "projected_points", "adp", "vor",
            "scarcity", "draft_score", "availability_next_pick", "value_grade",
        ):
            assert field in player


class TestPlayerEndpoint:
    def test_player_detail_shows_the_scoring_maths(self, ready):
        first = ready.get("/api/players", params={"limit": 1}).json()["players"][0]
        body = ready.get(f"/api/players/{first['espn_player_id']}").json()
        assert body["components"]
        assert body["reasons"]
        assert body["scoring_breakdown"]
        total = sum(item["points"] for item in body["scoring_breakdown"])
        assert round(total, 1) == pytest.approx(body["projected_points"], abs=0.2)

    def test_unknown_player_404s(self, ready):
        assert ready.get("/api/players/1").status_code == 404


class TestRecommendations:
    def test_best_pick_and_alternatives(self, ready):
        body = ready.get("/api/draft/recommendations", params={"limit": 10}).json()
        assert body["best_pick"]
        assert len(body["top_picks"]) == 10
        assert body["top_picks"][0]["espn_player_id"] == body["best_pick"]["espn_player_id"]

    def test_every_recommendation_explains_itself(self, ready):
        body = ready.get("/api/draft/recommendations", params={"limit": 10}).json()
        for player in body["top_picks"]:
            assert player["reasons"]
            assert player["why_now"] and player["why_wait"] and player["principal_risk"]

    def test_meta_exposes_the_model_internals(self, ready):
        meta = ready.get("/api/draft/recommendations").json()["meta"]
        assert meta["replacement"]["positions"]["RB"]["replacement_points"] > 0
        assert meta["scarcity"]["RB"]["explanation"]
        assert "position_decay" in meta


class TestTeamEndpoint:
    def test_empty_team(self, ready):
        body = ready.get("/api/team").json()
        assert body["picks_made"] == 0
        assert body["lineup"]["empty_slots"]
        assert body["remaining_needs"]

    def test_team_after_drafting(self, ready):
        for pick in (1, 24, 25):
            player = ready.get("/api/players", params={"limit": 1}).json()["players"][0]
            ready.post("/api/draft/pick",
                       json={"espn_player_id": player["espn_player_id"], "overall_pick": pick})
        body = ready.get("/api/team").json()
        assert body["picks_made"] == 3
        assert body["lineup"]["total_points"] > 0
        assert body["positional_strength"]
        assert 0 <= body["roster_construction_score"] <= 100
        assert body["highest_marginal_value"]["position"]


class TestSimulationEndpoint:
    def test_simulation_runs(self, ready):
        body = ready.post("/api/simulate", json={"simulations": 60, "my_slot": 4}).json()
        assert body["simulations"] == 60
        assert body["my_slot"] == 4
        assert body["strategy"]
        assert body["picks"]
        assert body["expected_roster_points"]["mean"] > 0

    def test_simulation_input_is_validated(self, ready):
        assert ready.post("/api/simulate", json={"simulations": 5}).status_code == 422
        assert ready.post("/api/simulate", json={"my_slot": 0}).status_code == 422


class TestFrontendServing:
    def test_spa_is_served_at_the_root(self, ready):
        response = ready.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_client_routes_fall_through_to_the_spa(self, ready):
        assert ready.get("/board").status_code == 200

    def test_unknown_api_routes_still_404(self, ready):
        assert ready.get("/api/does-not-exist").status_code == 404
