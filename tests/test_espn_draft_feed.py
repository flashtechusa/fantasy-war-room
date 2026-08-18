"""Parsing ESPN's `view=mDraftDetail` board.

The reason this path exists is in `docs/espn-api-comparison.md` §5.1: the
library we use for a completed draft refuses to return picks until ESPN flags
the draft as finished, which is precisely the window a live draft occupies.
These tests pin the behaviour that fixes it -- especially that the `drafted`
flag is *reported*, never used as a gate.

Nothing here touches the network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.espn.draft_feed import (
    DraftFeedSnapshot,
    fetch_draft_snapshot,
    parse_draft_detail,
)
from app.espn.http import EspnHttpClient, EspnHttpError


def pick(
    player_id: int,
    overall: int,
    round_id: int = 1,
    round_pick: int = 1,
    team_id: int = 1,
    **extra,
) -> dict:
    return {
        "playerId": player_id,
        "overallPickNumber": overall,
        "roundId": round_id,
        "roundPickNumber": round_pick,
        "teamId": team_id,
        **extra,
    }


def payload(picks: list[dict], **detail) -> dict:
    return {
        "draftDetail": {"drafted": False, "inProgress": False, "picks": picks, **detail},
        "settings": {"draftSettings": {"type": "SNAKE"}},
    }


class TestLiveDraft:
    def test_picks_are_returned_while_the_draft_is_running(self):
        """The whole point. `drafted` is false mid-draft; picks still exist."""
        snapshot = parse_draft_detail(
            payload(
                [pick(101, 1), pick(202, 2, round_pick=2, team_id=2)],
                drafted=False,
                inProgress=True,
            ),
            season=2026,
        )
        assert snapshot.pick_count == 2
        assert snapshot.in_progress is True
        assert snapshot.drafted is False
        assert snapshot.latest_pick_number == 2
        assert snapshot.started is True

    def test_a_completed_draft_reports_complete(self):
        snapshot = parse_draft_detail(
            payload([pick(101, 1)], drafted=True, inProgress=False), season=2026
        )
        assert snapshot.drafted is True
        assert snapshot.pick_count == 1

    def test_an_untouched_draft_is_empty_but_not_an_error(self):
        snapshot = parse_draft_detail(payload([]), season=2026)
        assert snapshot.picks == []
        assert snapshot.started is False
        assert snapshot.latest_pick_number == 0

    def test_a_missing_draft_detail_block_is_survivable(self):
        assert parse_draft_detail({}, season=2026).picks == []
        assert parse_draft_detail({"draftDetail": None}, season=2026).picks == []


class TestPickNumbers:
    def test_espns_overall_pick_number_is_preferred(self):
        """ESPN publishes it; deriving it breaks on traded picks and auctions."""
        # Round 2 pick 3 in a 10-team league would derive to 13. ESPN says 17,
        # because picks were traded. ESPN wins.
        snapshot = parse_draft_detail(
            payload([pick(1, 17, round_id=2, round_pick=3)]), season=2026, team_count=10
        )
        assert snapshot.picks[0]["overall_pick"] == 17

    def test_it_is_derived_only_when_espn_omits_it(self):
        raw = pick(1, 0, round_id=2, round_pick=3)
        raw.pop("overallPickNumber")
        snapshot = parse_draft_detail(payload([raw]), season=2026, team_count=10)
        assert snapshot.picks[0]["overall_pick"] == 13

    def test_position_is_the_last_resort(self):
        raw = {"playerId": 5}
        snapshot = parse_draft_detail(payload([raw]), season=2026, team_count=0)
        assert snapshot.picks[0]["overall_pick"] == 1

    def test_picks_come_back_in_draft_order(self):
        snapshot = parse_draft_detail(
            payload([pick(3, 9), pick(1, 2), pick(2, 5)]), season=2026
        )
        assert [p["overall_pick"] for p in snapshot.picks] == [2, 5, 9]


class TestPlaceholders:
    def test_unmade_picks_are_ignored(self):
        """ESPN emits rows for future picks with playerId 0 or -1."""
        snapshot = parse_draft_detail(
            payload([pick(101, 1), pick(0, 2), pick(-1, 3)]), season=2026
        )
        assert [p["espn_player_id"] for p in snapshot.picks] == [101]

    def test_non_dict_rows_are_ignored(self):
        snapshot = parse_draft_detail(payload([pick(101, 1), None, "junk"]), season=2026)
        assert snapshot.pick_count == 1


class TestPickFields:
    def test_auction_fields_are_kept(self):
        snapshot = parse_draft_detail(
            payload([pick(101, 1, bidAmount=57, nominatingTeamId=4, keeper=True)]),
            season=2026,
        )
        entry = snapshot.picks[0]
        assert entry["bid_amount"] == 57
        assert entry["nominating_team_id"] == 4
        assert entry["keeper"] is True

    def test_autodraft_is_flagged(self):
        snapshot = parse_draft_detail(
            payload([pick(101, 1, autoDraftTypeId=1)]), season=2026
        )
        assert snapshot.picks[0]["auto_pick"] is True

    def test_names_come_from_our_own_tables(self):
        """The endpoint carries ids only -- that is what keeps it pollable."""
        snapshot = parse_draft_detail(
            payload([pick(101, 1, teamId=3)]),
            season=2026,
            team_names={3: "Team Rocket"},
            player_names={101: "Test Runningback"},
        )
        assert snapshot.picks[0]["player_name"] == "Test Runningback"
        assert snapshot.picks[0]["team_name"] == "Team Rocket"

    def test_an_unknown_id_leaves_the_name_blank_rather_than_guessing(self):
        snapshot = parse_draft_detail(payload([pick(999, 1)]), season=2026)
        assert snapshot.picks[0]["player_name"] == ""

    def test_the_season_is_stamped_on_every_pick(self):
        snapshot = parse_draft_detail(payload([pick(1, 1)]), season=2026)
        assert snapshot.picks[0]["season"] == 2026


class TestFetch:
    """The request layer, driven by a stub transport instead of the network."""

    @staticmethod
    def client(handler) -> EspnHttpClient:
        return EspnHttpClient(
            swid="{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}",
            espn_s2="s2-value-that-is-long-enough",
            transport=httpx.MockTransport(handler),
        )

    def test_a_snapshot_records_its_endpoint_and_latency(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "mDraftDetail" in str(request.url)
            assert "/seasons/2026/segments/0/leagues/12345" in str(request.url)
            return httpx.Response(200, json=payload([pick(101, 1)], inProgress=True))

        snapshot = fetch_draft_snapshot(self.client(handler), 12345, 2026)
        assert snapshot.pick_count == 1
        assert "leagues/12345" in snapshot.endpoint
        assert snapshot.latency_ms >= 0
        assert snapshot.source == "espn_draft_detail"

    def test_cookies_are_sent_but_never_end_up_in_the_endpoint_string(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["cookie"] = request.headers.get("Cookie", "")
            return httpx.Response(200, json=payload([]))

        snapshot = fetch_draft_snapshot(self.client(handler), 1, 2026)
        assert "espn_s2=" in seen["cookie"] and "SWID=" in seen["cookie"]
        assert "espn_s2" not in snapshot.endpoint
        assert "AAAAAAAA" not in snapshot.endpoint

    def test_a_401_becomes_an_actionable_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={})

        with pytest.raises(EspnHttpError) as excinfo:
            fetch_draft_snapshot(self.client(handler), 1, 2026)
        assert "denied" in str(excinfo.value).lower()
        assert "reconnect" in str(excinfo.value).lower()

    def test_rate_limiting_says_so(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={})

        with pytest.raises(EspnHttpError, match="rate limiting"):
            fetch_draft_snapshot(self.client(handler), 1, 2026)

    def test_a_404_is_reported_as_no_such_league(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={})

        with pytest.raises(EspnHttpError, match="no such league"):
            fetch_draft_snapshot(self.client(handler), 1, 2026)

    def test_html_instead_of_json_does_not_crash(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>maintenance</html>")

        with pytest.raises(EspnHttpError, match="non-JSON"):
            fetch_draft_snapshot(self.client(handler), 1, 2026)

    def test_pre_2018_seasons_use_the_history_route(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "leagueHistory/999" in str(request.url)
            assert "seasonId=2015" in str(request.url)
            # The history route answers with a single-element array.
            return httpx.Response(200, json=[payload([pick(1, 1)])])

        snapshot = fetch_draft_snapshot(self.client(handler), 999, 2015)
        assert snapshot.pick_count == 1

    def test_a_player_filter_is_sent_as_a_header_not_a_query_param(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["filter"] = request.headers.get("x-fantasy-filter", "")
            return httpx.Response(200, json={})

        client = self.client(handler)
        client.league_view(1, 2026, "kona_player_info", player_filter={"players": {"limit": 5}})
        assert json.loads(seen["filter"]) == {"players": {"limit": 5}}


class TestSnapshotShape:
    def test_an_empty_snapshot_has_safe_defaults(self):
        snapshot = DraftFeedSnapshot()
        assert snapshot.pick_count == 0
        assert snapshot.latest_pick_number == 0
        assert snapshot.started is False
