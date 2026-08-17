"""FantasyPros as a second projection source.

Untested against the live API -- no key here, and the network path is blocked
in this environment -- so these exercise the parser and the matcher against
captured-shape fixtures. Those are the parts that can silently corrupt data;
the HTTP call either works or raises.
"""

from __future__ import annotations

import pytest

from app.projections.fantasypros import parse_projections
from app.projections.matching import (
    Candidate,
    PlayerMatcher,
    defence_key,
    normalise_name,
    normalise_team,
)


class TestNameNormalisation:
    @pytest.mark.parametrize(
        "left,right",
        [
            ("Kenneth Walker III", "Kenneth Walker"),
            ("T.J. Hockenson", "TJ Hockenson"),
            ("Marvin Harrison Jr.", "Marvin Harrison"),
            ("Amon-Ra St. Brown", "AmonRa St Brown"),
            ("D'Andre Swift", "DAndre Swift"),
            ("  Justin   Herbert ", "Justin Herbert"),
        ],
    )
    def test_spelling_differences_collapse(self, left, right):
        assert normalise_name(left) == normalise_name(right)

    def test_different_people_do_not_collapse(self):
        assert normalise_name("Michael Thomas") != normalise_name("Michael Pittman")

    def test_empty_input_is_safe(self):
        assert normalise_name(None) == ""
        assert normalise_name("") == ""


class TestTeamNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [("JAC", "JAX"), ("WSH", "WAS"), ("LAR", "LA"), ("OAK", "LV"), ("KC", "KC")],
    )
    def test_provider_abbreviations_are_reconciled(self, raw, expected):
        assert normalise_team(raw) == expected

    def test_case_and_punctuation_are_ignored(self):
        assert normalise_team("jax") == "JAX"


class TestDefenceMatching:
    def test_defence_naming_variants_collapse(self):
        assert defence_key("Jaguars D/ST") == defence_key("Jaguars DST")
        assert defence_key("Broncos Defense") == defence_key("Broncos")


class TestPlayerMatcher:
    def _matcher(self) -> PlayerMatcher:
        return PlayerMatcher(
            [
                Candidate(1, "Kenneth Walker III", "RB", "SEA"),
                Candidate(2, "Justin Herbert", "QB", "LAC"),
                Candidate(3, "Michael Thomas", "WR", "NO"),
                Candidate(4, "Michael Thomas", "WR", "BUF"),
                Candidate(5, "Jaguars D/ST", "DST", "JAX"),
            ]
        )

    def test_it_matches_across_a_name_variant(self):
        assert self._matcher().match("Kenneth Walker", "RB", "SEA").player_id == 1

    def test_it_matches_across_a_team_abbreviation_variant(self):
        assert self._matcher().match("Jaguars DST", "DST", "JAC").player_id == 5

    def test_a_team_change_does_not_block_a_match(self):
        """Providers update transactions at different times."""
        assert self._matcher().match("Justin Herbert", "QB", "DEN").player_id == 2

    def test_position_disagreement_is_not_a_match(self):
        assert self._matcher().match("Justin Herbert", "RB", "LAC") is None

    def test_a_duplicate_name_resolves_by_team(self):
        assert self._matcher().match("Michael Thomas", "WR", "BUF").player_id == 4

    def test_an_unresolvable_duplicate_is_refused_not_guessed(self):
        """A wrong match corrupts two players and is undetectable downstream."""
        matcher = self._matcher()
        assert matcher.match("Michael Thomas", "WR", "SEA") is None
        assert matcher.report["ambiguous_count"] == 1

    def test_an_unknown_player_is_reported(self):
        matcher = self._matcher()
        assert matcher.match("Nobody At All", "WR", "SEA") is None
        assert matcher.report["unmatched_count"] == 1


class TestParsing:
    def _payload(self) -> dict:
        return {
            "players": [
                {
                    "name": "Justin Herbert",
                    "position_id": "QB",
                    "team_id": "LAC",
                    "stats": {
                        "pass_yds": "4,250",
                        "pass_td": 28,
                        "pass_int": 10,
                        "rush_yds": 250,
                        "rush_td": 3,
                        "games": 17,
                    },
                },
                {
                    "name": "Kenneth Walker III",
                    "position_id": "RB",
                    "team_id": "SEA",
                    "stats": {"rush_yds": 1100, "rush_td": 9, "rec": 40, "rec_yds": 300},
                },
            ]
        }

    def test_stats_are_keyed_by_espn_stat_id(self):
        players = parse_projections(self._payload())
        herbert = players[0]
        assert herbert.raw_stats["3"] == 4250.0     # passing yards
        assert herbert.raw_stats["4"] == 28.0       # passing TDs
        assert herbert.raw_stats["20"] == 10.0      # interceptions thrown

    def test_comma_formatted_numbers_are_parsed(self):
        assert parse_projections(self._payload())[0].raw_stats["3"] == 4250.0

    def test_receptions_map_to_the_ppr_stat(self):
        walker = parse_projections(self._payload())[1]
        assert walker.raw_stats["53"] == 40.0

    def test_no_point_total_is_carried_over(self):
        """Their total is scored under their rules -- re-scoring is the point."""
        for player in parse_projections(self._payload()):
            assert not hasattr(player, "source_points")

    def test_games_are_captured_when_present(self):
        assert parse_projections(self._payload())[0].projected_games == 17.0

    def test_an_unrecognised_shape_returns_nothing_rather_than_guessing(self):
        assert parse_projections({"unexpected": True}) == []

    def test_alternative_list_keys_are_tolerated(self):
        payload = {"data": [{"name": "X", "position_id": "WR", "rec": 5}]}
        assert len(parse_projections(payload)) == 1

    def test_inline_stats_without_a_stats_object_still_parse(self):
        payload = {"players": [{"name": "Y", "position_id": "WR", "rec_yds": 900}]}
        assert parse_projections(payload)[0].raw_stats["42"] == 900.0


class TestImportEndpoint:
    def test_it_refuses_politely_without_a_key(self, client):
        client.post("/api/league/import")
        response = client.post("/api/league/projections/fantasypros")
        assert response.status_code == 400
        assert "key" in response.json()["detail"].lower()

    def test_the_key_is_never_returned_by_the_config_api(self, client):
        secret = "test-key-do-not-echo"
        client.put("/api/config", json={"fantasypros_api_key": secret})
        assert secret not in client.get("/api/config").text


class TestKeyIsActuallyStored:
    """The save endpoint accepted the key and threw it away.

    Pydantic drops unknown fields by default, and the request model had no
    `fantasypros_api_key`, so the API returned 200 while storing nothing and
    the UI reported success.
    """

    def test_saving_a_key_makes_it_stored(self, client):
        client.put("/api/config", json={"fantasypros_api_key": "abc123"})
        assert client.get("/api/config").json()["fantasypros_key_set"] is True

    def test_an_unset_key_reports_as_unset(self, client):
        assert client.get("/api/config").json()["fantasypros_key_set"] is False

    def test_the_import_becomes_reachable_once_a_key_exists(self, client):
        client.post("/api/league/import")
        assert client.post("/api/league/projections/fantasypros").status_code == 400
        client.put("/api/config", json={"fantasypros_api_key": "abc123"})
        # Now it gets past the key check and fails on the network instead.
        response = client.post("/api/league/projections/fantasypros")
        assert "key" not in response.json().get("detail", "").lower()

    def test_an_unknown_field_is_rejected_rather_than_dropped(self, client):
        """Failing loudly is what would have caught the original bug."""
        response = client.put("/api/config", json={"not_a_real_setting": "x"})
        assert response.status_code == 422

    def test_clearing_the_key_unsets_it(self, client):
        client.put("/api/config", json={"fantasypros_api_key": "abc123"})
        client.put("/api/config", json={"fantasypros_api_key": ""})
        assert client.get("/api/config").json()["fantasypros_key_set"] is False


class TestRealResponseShape:
    """Parsed from an actual API response, not their documentation.

    The first version of the stat map was written from the docs and had the
    wrong names for nearly every stat. Players matched, stat lines came back
    empty, and the import reported success -- the worst possible failure mode.
    This fixture is a verbatim slice of a real 2026 response.
    """

    PAYLOAD = {
        "season": "2026",
        "week": "0",
        "count": "132",
        "positions": "RB",
        "scoring": "STD",
        "players": [
            {
                "fpid": 22968,
                "mflid": 16162,
                "name": "Jahmyr Gibbs",
                "position_id": "RB",
                "team_id": "DET",
                "filename": "jahmyr-gibbs.php",
                "stats": {
                    "points": 301.77,
                    "points_ppr": 373.04,
                    "points_half": 337.41,
                    "rush_att": 274.73,
                    "rush_yds": 1382.03,
                    "rush_tds": 13.83,
                    "rush_yds_100": 0,
                    "rush_yds_200": 0,
                    "scrimage_yards_100": 0,
                    "scrimage_yards_200": 0,
                    "rec_rec": 71.27,
                    "rec_yds": 581.1,
                    "rec_tds": 4.13,
                    "rec_yds_100": 0,
                    "rec_yds_200": 0,
                    "fumbles": 1.13,
                    "ret_tds": 0,
                    "2pt_tds": 0,
                },
            }
        ],
    }

    def _gibbs(self):
        players = parse_projections(self.PAYLOAD)
        assert len(players) == 1
        return players[0]

    def test_the_player_is_identified(self):
        gibbs = self._gibbs()
        assert gibbs.name == "Jahmyr Gibbs"
        assert gibbs.position == "RB"
        assert gibbs.pro_team == "DET"

    def test_rushing_stats_land_on_the_right_ids(self):
        stats = self._gibbs().raw_stats
        assert stats["24"] == 1382.03    # rushing yards
        assert stats["25"] == 13.83      # rushing TDs

    def test_receptions_use_rec_rec_not_rec(self):
        """The specific mistake: `rec` does not exist in their payload."""
        assert self._gibbs().raw_stats["53"] == 71.27

    def test_receiving_stats_land_on_the_right_ids(self):
        stats = self._gibbs().raw_stats
        assert stats["42"] == 581.1      # receiving yards
        assert stats["43"] == 4.13       # receiving TDs

    def test_fumbles_are_captured(self):
        assert self._gibbs().raw_stats["72"] == 1.13

    def test_their_point_totals_are_never_stored(self):
        """points/points_ppr/points_half are scored under their rules."""
        values = set(self._gibbs().raw_stats.values())
        assert 301.77 not in values
        assert 373.04 not in values
        assert 337.41 not in values

    def test_bonus_thresholds_are_not_mistaken_for_stats(self):
        stats = self._gibbs().raw_stats
        # Every stored key must be a real ESPN stat id we mapped deliberately.
        assert all(key.isdigit() for key in stats)

    def test_a_real_line_produces_a_usable_number_of_stats(self):
        """The failure being guarded against was an empty stat line."""
        assert len(self._gibbs().raw_stats) >= 5
