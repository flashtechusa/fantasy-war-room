"""League discovery and team auto-detection.

Discovery is what removes the "find your league id" step, and team detection is
what removes "which of these twelve teams is yours". Both run off the SWID
cookie alone, so both are tested against realistic payloads with no network.
"""

from __future__ import annotations

import httpx
import pytest

from app.espn.discovery import (
    candidates_from_fan_profile,
    discover_leagues,
    league_preview,
    normalise_swid,
    parse_league_payload,
    rules_summary,
)
from app.espn.http import EspnHttpClient

MY_SWID = "{1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D}"
OTHER_SWID = "{99999999-8888-7777-6666-555555555555}"


def fan_entry(league_id: int, name: str, season: int = 2026, team_id: int = 3, size: int = 12):
    """One fantasy entry as ESPN's fan profile renders it."""
    return {
        "metaData": {
            "entry": {
                "entryId": team_id,
                "gameId": 1,
                "seasonId": season,
                "entryLocation": "Test",
                "entryNickname": "Team",
                "groups": [
                    {
                        "groupId": league_id,
                        "groupName": name,
                        "groupSize": size,
                        "groupManagerTeamId": team_id,
                    }
                ],
            }
        }
    }


def fan_profile(*entries) -> dict:
    return {"id": MY_SWID, "preferences": list(entries)}


def league_payload(
    league_id: int,
    name: str,
    season: int = 2026,
    size: int = 12,
    my_team: int | None = 3,
    picks: list | None = None,
    in_progress: bool = False,
) -> dict:
    teams = []
    for team_id in range(1, size + 1):
        owner = MY_SWID if team_id == my_team else OTHER_SWID
        teams.append(
            {
                "id": team_id,
                "name": f"Team {team_id}",
                "abbrev": f"T{team_id}",
                "primaryOwner": owner,
                "owners": [owner],
                "logo": "",
            }
        )
    return {
        "id": league_id,
        "seasonId": season,
        "members": [
            {"id": MY_SWID, "firstName": "Real", "lastName": "Owner"},
            {"id": OTHER_SWID, "firstName": "Someone", "lastName": "Else"},
        ],
        "teams": teams,
        "draftDetail": {
            "drafted": bool(picks) and not in_progress,
            "inProgress": in_progress,
            "picks": picks or [],
        },
        "settings": {
            "name": name,
            "size": size,
            "rosterSettings": {
                "lineupSlotCounts": {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1,
                                     "17": 1, "20": 7, "21": 1}
            },
            "scoringSettings": {
                "scoringType": "H2H_POINTS",
                "scoringItems": [
                    {"statId": 53, "points": 1.0, "pointsOverrides": {}},
                    {"statId": 4, "points": 4.0, "pointsOverrides": {}},
                ],
            },
            "draftSettings": {
                "type": "SNAKE",
                "pickOrder": list(range(1, size + 1)),
                "keeperCount": 2,
                "timePerSelection": 90,
            },
            "acquisitionSettings": {
                "isUsingAcquisitionBudget": True,
                "acquisitionBudget": 100,
                "waiverProcessDays": ["WED"],
                "waiverHours": 3,
            },
            "scheduleSettings": {
                "matchupPeriodCount": 14,
                "playoffTeamCount": 6,
                "playoffMatchupPeriodLength": 2,
                "playoffSeedingRule": "TOTAL_POINTS_SCORED",
            },
        },
    }


class TestNormaliseSwid:
    def test_braces_are_added_when_missing(self):
        assert normalise_swid("1a2b-3c4d") == "{1A2B-3C4D}"

    def test_an_already_wrapped_value_is_unchanged_apart_from_case(self):
        assert normalise_swid(MY_SWID) == MY_SWID.upper()

    def test_quotes_and_whitespace_are_stripped(self):
        assert normalise_swid(f'  "{MY_SWID}" ') == MY_SWID.upper()

    def test_empty_stays_empty(self):
        assert normalise_swid(None) == ""
        assert normalise_swid("  ") == ""


class TestFanProfileParsing:
    def test_one_league_is_found(self):
        found = candidates_from_fan_profile(fan_profile(fan_entry(111, "Stephen's League")))
        assert len(found) == 1
        assert found[0].league_id == 111
        assert found[0].name == "Stephen's League"
        assert found[0].team_id == 3

    def test_multiple_leagues_are_all_found(self):
        """A multi-league account is the case the picker exists for."""
        found = candidates_from_fan_profile(
            fan_profile(
                fan_entry(111, "Stephen's League"),
                fan_entry(222, "Work League", team_id=7, size=10),
                fan_entry(333, "Dynasty League", team_id=1),
            )
        )
        assert {c.league_id for c in found} == {111, 222, 333}
        assert {c.name for c in found} == {"Stephen's League", "Work League", "Dynasty League"}

    def test_other_sports_are_excluded(self):
        basketball = fan_entry(444, "Hoops League")
        basketball["metaData"]["entry"]["gameId"] = 2
        found = candidates_from_fan_profile(fan_profile(fan_entry(111, "Football"), basketball))
        assert [c.league_id for c in found] == [111]

    def test_a_season_filter_narrows_the_list(self):
        found = candidates_from_fan_profile(
            fan_profile(
                fan_entry(111, "This year", season=2026),
                fan_entry(112, "Last year", season=2025),
            ),
            season=2026,
        )
        assert [c.league_id for c in found] == [111]

    def test_a_url_hint_is_accepted_when_there_is_no_game_id(self):
        entry = fan_entry(555, "Legacy League")
        entry["metaData"]["entry"].pop("gameId")
        entry["metaData"]["entry"]["entryURL"] = "https://fantasy.espn.com/football/team"
        assert [c.league_id for c in candidates_from_fan_profile(fan_profile(entry))] == [555]

    def test_an_entry_with_no_sport_signal_is_kept_for_confirmation(self):
        """Better to confirm it against ESPN than to drop somebody's league."""
        entry = fan_entry(666, "Unknown Sport")
        entry["metaData"]["entry"].pop("gameId")
        assert [c.league_id for c in candidates_from_fan_profile(fan_profile(entry))] == [666]

    def test_a_payload_shaped_differently_still_yields_leagues(self):
        """The walk exists because ESPN has moved this structure before."""
        moved = {"data": {"somethingNew": [fan_entry(777, "Relocated")["metaData"]["entry"]]}}
        assert [c.league_id for c in candidates_from_fan_profile(moved)] == [777]

    def test_junk_is_not_an_error(self):
        assert candidates_from_fan_profile({}) == []
        assert candidates_from_fan_profile({"preferences": [None, 3, "x"]}) == []

    def test_duplicate_entries_collapse(self):
        profile = fan_profile(fan_entry(111, "Dupe"), fan_entry(111, "Dupe"))
        assert len(candidates_from_fan_profile(profile)) == 1


class TestTeamDetection:
    def test_the_team_matching_the_swid_is_mine(self):
        league = parse_league_payload(league_payload(111, "Test", my_team=5), 2026, MY_SWID)
        assert league.my_team_id == 5
        assert league.my_team_name == "Team 5"
        assert [t["espn_team_id"] for t in league.teams if t["is_mine"]] == [5]

    def test_no_match_leaves_the_team_unset_rather_than_guessing(self):
        league = parse_league_payload(league_payload(111, "Test", my_team=None), 2026, MY_SWID)
        assert league.my_team_id is None
        assert not any(t["is_mine"] for t in league.teams)

    def test_detection_is_case_and_brace_insensitive(self):
        payload = league_payload(111, "Test", my_team=2)
        payload["teams"][1]["primaryOwner"] = MY_SWID.strip("{}").lower()
        payload["teams"][1]["owners"] = [MY_SWID.strip("{}").lower()]
        league = parse_league_payload(payload, 2026, MY_SWID)
        assert league.my_team_id == 2

    def test_owner_display_names_come_from_the_members_join(self):
        league = parse_league_payload(league_payload(111, "Test", my_team=1), 2026, MY_SWID)
        assert league.teams[0]["owners"] == ["Real Owner"]

    def test_a_missing_swid_detects_nothing_but_still_parses(self):
        league = parse_league_payload(league_payload(111, "Test"), 2026, None)
        assert league.my_team_id is None
        assert len(league.teams) == 12

    def test_draft_slots_come_from_the_pick_order(self):
        league = parse_league_payload(league_payload(111, "Test"), 2026, MY_SWID)
        assert league.teams[0]["draft_slot"] == 1
        assert league.teams[3]["draft_slot"] == 4


class TestLeagueParsing:
    def test_the_headline_fields_are_read(self):
        league = parse_league_payload(league_payload(111, "Stephen's League", size=12), 2026, MY_SWID)
        assert league.league_id == 111
        assert league.name == "Stephen's League"
        assert league.team_count == 12
        assert league.season == 2026
        assert league.draft_type == "SNAKE"

    def test_ppr_is_read_from_the_rules_not_guessed(self):
        league = parse_league_payload(league_payload(111, "Test"), 2026, MY_SWID)
        assert league.is_ppr is True
        assert league.ppr_value == 1.0

    def test_an_in_progress_draft_is_reported_as_such(self):
        league = parse_league_payload(
            league_payload(111, "Test", picks=[{"playerId": 1}, {"playerId": 2}], in_progress=True),
            2026,
            MY_SWID,
        )
        assert league.draft_in_progress is True
        assert league.draft_completed is False
        assert league.draft_pick_count == 2

    def test_placeholder_picks_do_not_count(self):
        league = parse_league_payload(
            league_payload(111, "Test", picks=[{"playerId": 1}, {"playerId": 0}]),
            2026,
            MY_SWID,
        )
        assert league.draft_pick_count == 1

    def test_the_summary_contains_no_raw_settings_blob(self):
        league = parse_league_payload(league_payload(111, "Test"), 2026, MY_SWID)
        assert "settings" not in league.summary()

    def test_an_empty_payload_does_not_raise(self):
        league = parse_league_payload({}, 2026, MY_SWID)
        assert league.teams == []
        assert league.team_count == 0


class TestRulesSummary:
    def test_every_rule_group_the_confirm_step_shows_is_present(self):
        league = parse_league_payload(league_payload(111, "Test"), 2026, MY_SWID)
        rules = rules_summary(league)
        assert rules["roster_slots"] == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1,
                                         "DST": 1, "K": 1}
        assert rules["bench_slots"] == 7
        assert rules["ir_slots"] == 1
        assert rules["uses_faab"] is True
        assert rules["acquisition_budget"] == 100
        assert rules["waiver_process_days"] == ["WED"]
        assert rules["playoff_team_count"] == 6
        assert rules["playoff_matchup_length"] == 2
        assert rules["regular_season_weeks"] == 14
        assert rules["keeper_count"] == 2
        assert rules["draft_type"] == "SNAKE"
        assert len(rules["draft_order"]) == 12


class TestDiscoverLeagues:
    @staticmethod
    def client(handler) -> EspnHttpClient:
        return EspnHttpClient(
            swid=MY_SWID, espn_s2="s2-value", transport=httpx.MockTransport(handler)
        )

    def test_three_leagues_are_discovered_and_confirmed(self):
        """The "Found 3 ESPN leagues" case, end to end."""
        names = {111: "Stephen's League", 222: "Work League", 333: "Dynasty League"}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "fan.api.espn.com" in url:
                return httpx.Response(
                    200,
                    json=fan_profile(
                        fan_entry(111, names[111]),
                        fan_entry(222, names[222], team_id=7, size=10),
                        fan_entry(333, names[333], team_id=1),
                    ),
                )
            league_id = int(url.split("/leagues/")[1].split("?")[0])
            size = 10 if league_id == 222 else 12
            my_team = {111: 3, 222: 7, 333: 1}[league_id]
            return httpx.Response(
                200, json=league_payload(league_id, names[league_id], size=size, my_team=my_team)
            )

        leagues, warnings = discover_leagues(self.client(handler), season=2026)
        assert len(leagues) == 3
        assert warnings == []
        assert {lg.name for lg in leagues} == set(names.values())
        # Every one of them auto-detects the right team.
        assert {lg.league_id: lg.my_team_id for lg in leagues} == {111: 3, 222: 7, 333: 1}
        assert next(lg for lg in leagues if lg.league_id == 222).team_count == 10

    def test_the_swid_is_percent_encoded_into_the_fan_path(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=fan_profile())

        discover_leagues(self.client(handler), season=2026)
        assert "%7B" in seen["url"] and "%7D" in seen["url"]

    def test_a_league_that_will_not_load_is_reported_not_hidden(self):
        """"We found 2 of your 3" is a different message from "you have 2"."""

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "fan.api.espn.com" in url:
                return httpx.Response(
                    200, json=fan_profile(fan_entry(111, "Good"), fan_entry(222, "Broken"))
                )
            if "/leagues/222" in url:
                return httpx.Response(401, json={})
            return httpx.Response(200, json=league_payload(111, "Good"))

        leagues, warnings = discover_leagues(self.client(handler), season=2026)
        assert [lg.league_id for lg in leagues] == [111]
        assert any("Broken" in w for w in warnings)

    def test_a_failed_fan_lookup_degrades_to_manual_entry(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "fan.api.espn.com" in str(request.url):
                return httpx.Response(500, json={})
            return httpx.Response(200, json=league_payload(444, "Typed by hand"))

        leagues, warnings = discover_leagues(
            self.client(handler), season=2026, extra_league_ids=[444]
        )
        assert [lg.league_id for lg in leagues] == [444]
        assert any("by hand" in w for w in warnings)

    def test_a_manual_id_is_not_duplicated_when_it_was_also_discovered(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "fan.api.espn.com" in str(request.url):
                return httpx.Response(200, json=fan_profile(fan_entry(111, "Both ways")))
            return httpx.Response(200, json=league_payload(111, "Both ways"))

        leagues, _ = discover_leagues(self.client(handler), season=2026, extra_league_ids=[111])
        assert len(leagues) == 1

    def test_no_leagues_is_an_empty_list_not_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fan_profile())

        leagues, warnings = discover_leagues(self.client(handler), season=2026)
        assert leagues == []
        assert warnings == []

    def test_preview_reads_a_league_in_one_request(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=league_payload(111, "One shot"))

        league = league_preview(self.client(handler), 111, 2026, swid=MY_SWID)
        assert len(calls) == 1
        for view in ("mSettings", "mTeam", "mDraftDetail"):
            assert view in calls[0]
        assert league.name == "One shot"
        assert league.my_team_id == 3


@pytest.mark.parametrize("season", [2026, 2015])
def test_the_right_route_is_used_for_the_season(season):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        body = league_payload(111, "Test", season=season)
        return httpx.Response(200, json=[body] if season < 2018 else body)

    client = EspnHttpClient(swid=MY_SWID, espn_s2="x", transport=httpx.MockTransport(handler))
    league_preview(client, 111, season)
    if season < 2018:
        assert "leagueHistory/111" in seen["url"]
    else:
        assert f"/seasons/{season}/segments/0/leagues/111" in seen["url"]
