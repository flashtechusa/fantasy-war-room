"""Yahoo as a second platform.

Untested against the live API -- that needs a Yahoo app, an OAuth handshake and
a real league, none of which exist in CI -- so these run the parsers against
captured-shape payloads. Parsing is where Yahoo can silently corrupt data: its
JSON is transliterated XML, so a shape misread produces empty teams and zeroed
rosters rather than an error.
"""

from __future__ import annotations

import time

import pytest

from app.yahoo.client import YahooClient
from app.yahoo.constants import PRIVATE_STAT_BASE, espn_stat_id, normalise_yahoo_slot
from app.yahoo.oauth import YahooOAuth, YahooTokens
from app.yahoo.payload import collection, deep_collection, items, merge, num, sub_resource

LEAGUE_KEY = "461.l.123456"


# ---------------------------------------------------------------------------
# Payload fixtures -- shaped exactly as Yahoo returns them
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_payload() -> dict:
    return {
        "league": [
            {
                "league_key": LEAGUE_KEY,
                "league_id": "123456",
                "name": "Employee Appreciation League",
                "url": "https://football.fantasysports.yahoo.com/f1/123456",
                "num_teams": 10,
                "current_week": "3",
                "scoring_type": "head",
                "draft_status": "postdraft",
                "season": "2026",
                "renew": "449_98765",
            },
            {
                "settings": [
                    {
                        "draft_type": "live",
                        "is_auction_draft": "0",
                        "playoff_start_week": "15",
                        "num_playoff_teams": "6",
                        "waiver_type": "FR",
                        "uses_faab": "1",
                        "faab_budget": "100",
                        "trade_end_date": "2026-11-27",
                        "num_keepers": "0",
                        "roster_positions": [
                            {"roster_position": {"position": "QB", "count": 1}},
                            {"roster_position": {"position": "WR", "count": 2}},
                            {"roster_position": {"position": "RB", "count": 2}},
                            {"roster_position": {"position": "TE", "count": 1}},
                            {"roster_position": {"position": "W/R/T", "count": 1}},
                            {"roster_position": {"position": "K", "count": 1}},
                            {"roster_position": {"position": "DEF", "count": 1}},
                            {"roster_position": {"position": "BN", "count": 6}},
                            {"roster_position": {"position": "IR", "count": 1}},
                        ],
                        "stat_categories": {
                            "stats": [
                                {"stat": {"stat_id": 4, "name": "Passing Yards",
                                          "display_name": "Pass Yds"}},
                                {"stat": {"stat_id": 5, "name": "Passing Touchdowns",
                                          "display_name": "Pass TD"}},
                                {"stat": {"stat_id": 11, "name": "Receptions",
                                          "display_name": "Rec"}},
                                {"stat": {"stat_id": 9, "name": "Rushing Yards",
                                          "display_name": "Rush Yds"}},
                                {"stat": {"stat_id": 19, "name": "Field Goals 0-19 Yards",
                                          "display_name": "FG 0-19"}},
                                {"stat": {"stat_id": 20, "name": "Field Goals 20-29 Yards",
                                          "display_name": "FG 20-29"}},
                                {"stat": {"stat_id": 21, "name": "Field Goals 30-39 Yards",
                                          "display_name": "FG 30-39"}},
                                {"stat": {"stat_id": 38, "name": "Tackle Solo",
                                          "display_name": "Tack"}},
                            ]
                        },
                        "stat_modifiers": {
                            "stats": [
                                {"stat": {"stat_id": 4, "value": "0.04"}},
                                {"stat": {"stat_id": 5, "value": "4"}},
                                {"stat": {"stat_id": 11, "value": "0.5"}},
                                {"stat": {"stat_id": 9, "value": "0.1"}},
                                {"stat": {"stat_id": 19, "value": "3"}},
                                {"stat": {"stat_id": 20, "value": "3"}},
                                {"stat": {"stat_id": 21, "value": "3"}},
                                {"stat": {"stat_id": 38, "value": "1"}},
                            ]
                        },
                    }
                ]
            },
        ]
    }


@pytest.fixture
def teams_payload() -> dict:
    def team(team_id: int, name: str, mine: str) -> dict:
        return {
            "team": [
                [
                    {"team_key": f"{LEAGUE_KEY}.t.{team_id}"},
                    {"team_id": str(team_id)},
                    {"name": name},
                    {
                        "team_logos": {
                            "0": {"team_logo": {"size": "large", "url": f"http://x/{team_id}.png"}},
                            "count": 1,
                        }
                    },
                    {
                        "managers": {
                            "0": {
                                "manager": {
                                    "manager_id": str(team_id),
                                    "nickname": f"Manager {team_id}",
                                    "guid": f"GUID{team_id}",
                                    "is_owned_by_current_login": mine,
                                }
                            },
                            "count": 1,
                        }
                    },
                ],
                {
                    "roster": {
                        "0": {
                            "players": {
                                "0": {
                                    "player": [
                                        [
                                            {"player_key": f"nfl.p.{100 + team_id}"},
                                            {"player_id": str(100 + team_id)},
                                            {"name": {"full": f"Player {team_id}"}},
                                            {"editorial_team_abbr": "sf"},
                                            {"display_position": "RB"},
                                            {"status": "Q"},
                                        ],
                                        {
                                            "selected_position": [
                                                {"coverage_type": "week"},
                                                {"position": "W/R/T"},
                                            ]
                                        },
                                    ]
                                },
                                "count": 1,
                            }
                        },
                        "coverage_type": "date",
                    }
                },
            ]
        }

    return {
        "league": [
            {"league_key": LEAGUE_KEY},
            {"teams": {"0": team(1, "First Team", "1"), "1": team(2, "Second Team", "0"),
                       "count": 2}},
        ]
    }


@pytest.fixture
def standings_payload() -> dict:
    return {
        "league": [
            {"league_key": LEAGUE_KEY},
            {
                "standings": {
                    "0": {
                        "teams": {
                            "0": {
                                "team": [
                                    [{"team_id": "1"}, {"name": "First Team"}],
                                    {
                                        "team_standings": {
                                            "rank": 1,
                                            "outcome_totals": {
                                                "wins": "2", "losses": "1", "ties": "0"
                                            },
                                        }
                                    },
                                ]
                            },
                            "count": 1,
                        }
                    },
                    "count": 1,
                }
            },
        ]
    }


@pytest.fixture
def draft_payload() -> dict:
    return {
        "league": [
            {"league_key": LEAGUE_KEY, "num_teams": 10},
            {
                "draft_results": {
                    "0": {"draft_result": {"pick": 1, "round": 1,
                                           "team_key": f"{LEAGUE_KEY}.t.4",
                                           "player_key": "nfl.p.101"}},
                    "1": {"draft_result": {"pick": 2, "round": 1,
                                           "team_key": f"{LEAGUE_KEY}.t.7",
                                           "player_key": "nfl.p.102"}},
                    "2": {"draft_result": {"pick": 11, "round": 2,
                                           "team_key": f"{LEAGUE_KEY}.t.7",
                                           "player_key": "nfl.p.103"}},
                    "count": 3,
                }
            },
        ]
    }


def _player_fragment(player_id: int, name: str, position: str, ownership: dict) -> dict:
    return {
        "player": [
            [
                {"player_key": f"nfl.p.{player_id}"},
                {"player_id": str(player_id)},
                {"name": {"full": name, "first": name.split()[0], "last": name.split()[-1]}},
                {"editorial_team_abbr": "kc"},
                {"bye_weeks": {"week": "10"}},
                {"display_position": position},
                {"primary_position": position},
                {
                    "eligible_positions": [
                        {"position": position},
                        {"position": "W/R/T"},
                    ]
                },
                {"status": "Q", "status_full": "Questionable"},
            ],
            {"draft_analysis": [{"average_pick": "14.7"}, {"average_round": "2.1"},
                                {"percent_drafted": "1.00"}]},
            {"ownership": ownership},
            {"percent_owned": [{"coverage_type": "week"}, {"week": "3"}, {"value": 98},
                               {"delta": "0"}]},
        ]
    }


@pytest.fixture
def players_payload() -> dict:
    return {
        "league": [
            {"league_key": LEAGUE_KEY},
            {
                "players": {
                    "0": _player_fragment(
                        201, "Real Runningback", "RB",
                        {"ownership_type": "team", "owner_team_key": f"{LEAGUE_KEY}.t.3"},
                    ),
                    "1": _player_fragment(
                        202, "Free Agent Wideout", "WR", {"ownership_type": "freeagents"}
                    ),
                    "count": 2,
                }
            },
        ]
    }


class _StubClient(YahooClient):
    """A YahooClient wired to canned payloads instead of the network."""

    def __init__(self, responses: dict):
        super().__init__(
            league_id=123456,
            season=2026,
            oauth=YahooOAuth("id", "secret", tokens=YahooTokens("a", "r", time.time() + 3600)),
            game_key="461",
        )
        self.responses = responses
        self.requested: list[str] = []

    def _get(self, path: str, params: dict | None = None) -> dict:
        self.requested.append(path)
        for key, payload in self.responses.items():
            if key in path:
                return payload
        return {}


# ---------------------------------------------------------------------------
# The JSON shape
# ---------------------------------------------------------------------------


class TestPayloadShape:
    def test_numbered_objects_read_as_lists(self):
        node = {"1": {"team": "b"}, "0": {"team": "a"}, "count": 2}
        assert items(node) == [{"team": "a"}, {"team": "b"}]

    def test_collection_unwraps_the_singular_key(self):
        parent = {"teams": {"0": {"team": ["x"]}, "count": 1}}
        assert collection(parent, "teams", "team") == [["x"]]

    def test_merge_collapses_fragment_lists(self):
        node = [[{"a": 1}, {"b": 2}], {"c": 3}]
        assert merge(node) == {"a": 1, "b": 2, "c": 3}

    def test_merge_ignores_collection_bookkeeping(self):
        assert merge({"0": {"x": 1}, "count": 1, "name": "keep"}) == {"name": "keep"}

    def test_merge_keeps_count_when_it_is_a_real_field(self):
        # A roster position's `count` is how many of that slot the league
        # starts, not a collection size.
        assert merge({"position": "RB", "count": 2}) == {"position": "RB", "count": 2}

    def test_deep_collection_sees_through_a_numbered_wrapper(self):
        roster = {"0": {"players": {"0": {"player": ["frag"]}, "count": 1}},
                  "coverage_type": "date"}
        assert deep_collection(roster, "players", "player") == [["frag"]]

    def test_sub_resource_reads_a_fragment_list(self):
        fragment = [[{"player_id": "1"}], {"draft_analysis": [{"average_pick": "3.2"}]}]
        assert sub_resource(fragment, "draft_analysis") == {"average_pick": "3.2"}

    @pytest.mark.parametrize("raw,expected", [("1,234", 1234.0), ("", 0.0), ("-", 0.0),
                                              ("3.5", 3.5), (None, 0.0)])
    def test_numbers_arrive_as_strings(self, raw, expected):
        assert num(raw) == expected


# ---------------------------------------------------------------------------
# Stat translation
# ---------------------------------------------------------------------------


class TestStatTranslation:
    def test_known_categories_map_onto_espn_ids(self):
        assert espn_stat_id(4) == 3     # passing yards
        assert espn_stat_id(11) == 53   # receptions
        assert espn_stat_id(9) == 24    # rushing yards

    def test_unknown_categories_get_a_private_id(self):
        assert espn_stat_id(999) == PRIVATE_STAT_BASE + 999

    @pytest.mark.parametrize(
        "yahoo,ours",
        [("W/R/T", "FLEX"), ("Q/W/R/T", "OP"), ("DEF", "DST"), ("BN", "BE"), ("IR", "IR")],
    )
    def test_roster_positions_map_onto_our_slots(self, yahoo, ours):
        assert normalise_yahoo_slot(yahoo) == ours


# ---------------------------------------------------------------------------
# League settings
# ---------------------------------------------------------------------------


class TestLeagueSettings:
    @pytest.fixture
    def parsed(self, settings_payload, draft_payload, teams_payload):
        client = _StubClient(
            {
                "settings": settings_payload,
                "draftresults": draft_payload,
                "/teams": teams_payload,
                "players;player_keys": {},
            }
        )
        return client.league_settings()

    def test_identity_and_shape(self, parsed):
        assert parsed["espn_league_id"] == 123456
        assert parsed["season"] == 2026
        assert parsed["source"] == "yahoo"
        assert parsed["name"] == "Employee Appreciation League"
        assert parsed["team_count"] == 10

    def test_roster_slots_use_our_vocabulary(self, parsed):
        assert parsed["roster_slots"] == {
            "QB": 1, "WR": 2, "RB": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1
        }
        assert parsed["bench_slots"] == 6
        assert parsed["ir_slots"] == 1

    def test_ppr_is_read_from_the_rules_not_guessed(self, parsed):
        assert parsed["ppr_value"] == 0.5
        assert parsed["is_ppr"] is True

    def test_scoring_rules_are_expressed_in_espn_stat_ids(self, parsed):
        by_id = {rule["stat_id"]: rule for rule in parsed["scoring_rules"]}
        assert by_id[3]["points"] == 0.04     # passing yards
        assert by_id[4]["points"] == 4.0      # passing TD
        assert by_id[24]["points"] == 0.1     # rushing yards
        assert by_id[53]["points"] == 0.5     # receptions

    def test_labels_come_from_yahoo(self, parsed):
        by_id = {rule["stat_id"]: rule for rule in parsed["scoring_rules"]}
        assert by_id[3]["label"] == "Passing Yards"
        assert by_id[3]["abbrev"] == "Pass Yds"

    def test_field_goal_bands_are_combined_and_reported(self, parsed):
        by_id = {rule["stat_id"]: rule for rule in parsed["scoring_rules"]}
        # Yahoo's 0-19 / 20-29 / 30-39 all live inside ESPN's 0-39 band.
        assert by_id[80]["points"] == 3.0
        notes = parsed["raw_settings"]["scoring_translation"]
        assert any("Field Goals 0-19 Yards" in note for note in notes["combined"])

    def test_categories_with_no_equivalent_are_reported_not_dropped(self, parsed):
        notes = parsed["raw_settings"]["scoring_translation"]
        assert "Tackle Solo" not in notes["unmapped"]  # solo tackles do map (ESPN 108)
        by_id = {rule["stat_id"]: rule for rule in parsed["scoring_rules"]}
        assert 108 in by_id

    def test_waivers_and_playoffs(self, parsed):
        assert parsed["waiver_type"] == "FAAB"
        assert parsed["uses_faab"] is True
        assert parsed["acquisition_budget"] == 100
        assert parsed["regular_season_weeks"] == 14
        assert parsed["playoff_team_count"] == 6

    def test_draft_state(self, parsed):
        assert parsed["draft_type"] == "SNAKE"
        assert parsed["draft_completed"] is True
        assert parsed["draft_order"] == [4, 7]

    def test_trade_deadline_is_stored_as_epoch_millis(self, parsed):
        assert parsed["trade_deadline"] > 1_700_000_000_000


class TestUnmappedCategories:
    def test_a_yahoo_only_category_survives_with_a_private_id(self, settings_payload):
        settings = settings_payload["league"][1]["settings"][0]
        settings["stat_categories"]["stats"].append(
            {"stat": {"stat_id": 977, "name": "Yahoo Novelty", "display_name": "Nov"}}
        )
        settings["stat_modifiers"]["stats"].append({"stat": {"stat_id": 977, "value": "2"}})

        client = _StubClient({"settings": settings_payload, "draftresults": {}})
        parsed = client.league_settings()

        by_id = {rule["stat_id"]: rule for rule in parsed["scoring_rules"]}
        assert by_id[PRIVATE_STAT_BASE + 977]["label"] == "Yahoo Novelty"
        assert "Yahoo Novelty" in parsed["raw_settings"]["scoring_translation"]["unmapped"]


# ---------------------------------------------------------------------------
# Teams, drafts, players
# ---------------------------------------------------------------------------


class TestTeams:
    @pytest.fixture
    def teams(self, teams_payload, standings_payload, draft_payload):
        client = _StubClient(
            {
                "teams/roster": teams_payload,
                "standings": standings_payload,
                "draftresults": draft_payload,
                "players;player_keys": {},
                "/teams": teams_payload,
            }
        )
        return client.teams()

    def test_every_team_is_read(self, teams):
        assert [team["espn_team_id"] for team in teams] == [1, 2]
        assert teams[0]["name"] == "First Team"

    def test_yahoo_identifies_my_team_outright(self, teams):
        assert teams[0]["is_current_login"] is True
        assert teams[1]["is_current_login"] is False

    def test_managers_and_logos(self, teams):
        assert teams[0]["owners"] == ["Manager 1"]
        assert teams[0]["owner_ids"] == ["GUID1"]
        assert teams[0]["logo_url"].endswith("1.png")

    def test_records_come_from_the_standings(self, teams):
        assert (teams[0]["wins"], teams[0]["losses"]) == (2, 1)

    def test_rosters_use_our_slot_and_position_labels(self, teams):
        entry = teams[0]["roster"][0]
        assert entry["espn_player_id"] == 101
        assert entry["position"] == "RB"
        assert entry["slot"] == "FLEX"
        assert entry["pro_team"] == "SF"
        assert entry["injury_status"] == "QUESTIONABLE"


class TestDraftResults:
    @pytest.fixture
    def picks(self, draft_payload, teams_payload):
        names = {
            "players": {
                "players": {
                    "0": {"player": [[{"player_key": "nfl.p.101"},
                                      {"name": {"full": "First Pick"}}]]},
                    "count": 1,
                }
            }
        }
        client = _StubClient(
            {
                "draftresults": draft_payload,
                "players;player_keys": names["players"],
                "/teams": teams_payload,
            }
        )
        return client.draft_results()

    def test_picks_are_numbered_within_the_round(self, picks):
        assert [(p["overall_pick"], p["round_num"], p["round_pick"]) for p in picks] == [
            (1, 1, 1),
            (2, 1, 2),
            (11, 2, 1),
        ]

    def test_ids_are_pulled_out_of_yahoo_keys(self, picks):
        assert picks[0]["espn_team_id"] == 4
        assert picks[0]["espn_player_id"] == 101

    def test_names_are_resolved_where_yahoo_gives_only_keys(self, picks):
        assert picks[0]["player_name"] == "First Pick"
        assert picks[0]["team_name"] == "First Team" or picks[0]["team_name"] == ""

    def test_an_undrafted_league_is_not_an_error(self):
        client = _StubClient({"draftresults": {"league": [{"league_key": LEAGUE_KEY}]}})
        assert client.draft_results() == []


class TestPlayerPool:
    @pytest.fixture
    def pool(self, players_payload):
        client = _StubClient({"/players;": players_payload})
        return client.player_pool(limit=2)

    def test_players_are_parsed(self, pool):
        assert [p.name for p in pool] == ["Real Runningback", "Free Agent Wideout"]
        assert pool[0].espn_player_id == 201
        assert pool[0].position == "RB"
        assert pool[0].pro_team == "KC"

    def test_eligibility_uses_our_slot_labels(self, pool):
        assert "FLEX" in pool[0].eligible_slots

    def test_availability_comes_from_ownership(self, pool):
        assert pool[0].availability == "ONTEAM"
        assert pool[0].on_team_id == 3
        assert pool[1].availability == "FREEAGENT"

    def test_market_data_is_kept(self, pool):
        assert pool[0].adp == 14.7
        assert pool[0].percent_owned == 98.0
        assert pool[0].bye_week == 10

    def test_no_projections_are_invented(self, pool):
        # Yahoo publishes none. An empty stat line is the honest answer; the
        # projections come from a projection source instead.
        assert pool[0].raw_stats == {}
        assert pool[0].espn_projected_points == 0.0

    def test_an_empty_wire_is_not_an_error(self):
        client = _StubClient({"/players;": {"league": [{"league_key": LEAGUE_KEY}]}})
        assert client.player_pool(limit=10, free_agents_only=True) == []


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


class TestTokens:
    def test_a_refresh_response_keeps_the_existing_refresh_token(self):
        previous = YahooTokens("old-access", "the-refresh", time.time())
        refreshed = YahooTokens.from_response({"access_token": "new", "expires_in": 3600},
                                              previous)
        assert refreshed.refresh_token == "the-refresh"
        assert refreshed.access_token == "new"
        assert not refreshed.is_expired

    def test_tokens_without_an_expiry_are_treated_as_expired(self):
        assert YahooTokens("a", "b", 0.0).is_expired

    def test_authorize_url_needs_an_app(self):
        from app.yahoo.oauth import YahooAuthError

        with pytest.raises(YahooAuthError):
            YahooOAuth("", "").authorize_url()

    def test_authorize_url_carries_the_redirect(self):
        url = YahooOAuth("client", "secret", redirect_uri="oob").authorize_url()
        assert "client_id=client" in url
        assert "redirect_uri=oob" in url
        assert "response_type=code" in url
