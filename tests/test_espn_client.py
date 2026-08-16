"""ESPN integration.

No network: we exercise the parsing of realistic ESPN payload shapes and the
translation of `espn-api`'s exceptions into messages a user can act on.
"""

from __future__ import annotations

import pytest
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)

from app.config import Settings
from app.espn.client import EspnClient, EspnConnectionError
from app.espn.constants import normalise_position, normalise_slot_label, stat_label


@pytest.fixture
def client() -> EspnClient:
    return EspnClient(league_id=123456, season=2026)


class TestConnectionErrors:
    """A failed connection must say what to do about it."""

    def _client_raising(self, monkeypatch, exception):
        def boom(*_args, **_kwargs):
            raise exception

        monkeypatch.setattr("app.espn.client.EspnLeague", boom)
        return EspnClient(league_id=123456, season=2026)

    def test_access_denied_without_cookies_asks_for_them(self, monkeypatch):
        client = self._client_raising(monkeypatch, ESPNAccessDenied("denied"))
        with pytest.raises(EspnConnectionError) as info:
            client.connect()
        assert "FWR_ESPN_SWID" in str(info.value)

    def test_access_denied_with_cookies_suggests_they_expired(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise ESPNAccessDenied("denied")

        monkeypatch.setattr("app.espn.client.EspnLeague", boom)
        client = EspnClient(123456, 2026, swid="{abc}", espn_s2="cookie")
        with pytest.raises(EspnConnectionError) as info:
            client.connect()
        assert "expired" in str(info.value)

    def test_invalid_league_names_the_settings_to_check(self, monkeypatch):
        client = self._client_raising(monkeypatch, ESPNInvalidLeague("nope"))
        with pytest.raises(EspnConnectionError) as info:
            client.connect()
        assert "FWR_ESPN_LEAGUE_ID" in str(info.value)

    def test_unknown_espn_error_is_wrapped(self, monkeypatch):
        client = self._client_raising(monkeypatch, ESPNUnknownError("500"))
        with pytest.raises(EspnConnectionError):
            client.connect()

    def test_network_failure_is_wrapped(self, monkeypatch):
        client = self._client_raising(monkeypatch, ConnectionError("no route to host"))
        with pytest.raises(EspnConnectionError) as info:
            client.connect()
        assert "Could not reach ESPN" in str(info.value)

    def test_connection_is_cached(self, monkeypatch):
        calls = []

        class FakeLeague:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("app.espn.client.EspnLeague", FakeLeague)
        client = EspnClient(123456, 2026)
        client.connect()
        client.connect()
        assert len(calls) == 1


class TestCredentialHandling:
    def test_private_auth_requires_both_cookies(self):
        assert not EspnClient(1, 2026, swid="{a}").is_private_auth
        assert not EspnClient(1, 2026, espn_s2="b").is_private_auth
        assert EspnClient(1, 2026, swid="{a}", espn_s2="b").is_private_auth

    def test_swid_braces_are_added_when_missing(self):
        settings = Settings(espn_swid="ABC-123")
        assert settings.espn_swid == "{ABC-123}"

    def test_swid_braces_are_preserved(self):
        assert Settings(espn_swid="{ABC-123}").espn_swid == "{ABC-123}"

    def test_blank_credentials_become_none(self):
        settings = Settings(espn_swid="   ", espn_s2="")
        assert settings.espn_swid is None
        assert settings.espn_s2 is None

    def test_credentials_are_never_defaulted(self):
        settings = Settings(_env_file=None)
        assert settings.espn_swid is None
        assert settings.espn_s2 is None
        assert not settings.has_espn_credentials

    def test_bare_espn_env_names_are_accepted(self, monkeypatch, tmp_path):
        """Pasting the conventional ESPN_* names should just work."""
        monkeypatch.setenv("ESPN_LEAGUE_ID", "11507")
        monkeypatch.setenv("ESPN_YEAR", "2026")
        monkeypatch.setenv("ESPN_SWID", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
        monkeypatch.setenv("ESPN_S2", "cookie-value")
        (tmp_path / ".env").write_text("")

        settings = Settings(_env_file=str(tmp_path / ".env"))
        assert settings.espn_league_id == 11507
        assert settings.espn_season == 2026
        assert settings.espn_swid == "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"
        assert settings.has_espn_credentials

    def test_prefixed_names_win_over_bare_ones(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ESPN_LEAGUE_ID", "11507")
        monkeypatch.setenv("FWR_ESPN_LEAGUE_ID", "22222")
        (tmp_path / ".env").write_text("")
        assert Settings(_env_file=str(tmp_path / ".env")).espn_league_id == 22222


class TestSlotParsing:
    def test_slot_counts_are_labelled_by_id(self, client, espn_settings_payload):
        counts = client._parse_slot_counts(espn_settings_payload["rosterSettings"])
        assert counts == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1,
                          "DST": 1, "K": 1, "BE": 7, "IR": 1}

    def test_zero_count_slots_are_dropped(self, client, espn_settings_payload):
        counts = client._parse_slot_counts(espn_settings_payload["rosterSettings"])
        assert "RB/WR" not in counts

    def test_bench_and_ir_are_split_out_of_starters(self, client, espn_settings_payload):
        counts = client._parse_slot_counts(espn_settings_payload["rosterSettings"])
        starters, bench, ir = client._split_slots(counts)
        assert bench == 7
        assert ir == 1
        assert "BE" not in starters and "IR" not in starters
        assert sum(starters.values()) == 9


class TestScoringParsing:
    def test_rules_are_parsed_with_labels(self, client, espn_settings_payload):
        rules = client._parse_scoring_rules(espn_settings_payload["scoringSettings"], None)
        by_id = {rule["stat_id"]: rule for rule in rules}
        assert by_id[4]["points"] == 4.0
        assert by_id[4]["abbrev"] == "PTD"
        assert by_id[20]["points"] == -2.0

    def test_ppr_is_read_not_guessed(self, client, espn_settings_payload):
        rules = client._parse_scoring_rules(espn_settings_payload["scoringSettings"], None)
        assert client._detect_ppr(rules) == 1.0

    def test_standard_scoring_detected_as_zero_ppr(self, client):
        rules = [{"stat_id": 53, "points": 0.0, "abbrev": "", "label": ""}]
        assert client._detect_ppr(rules) == 0.0

    def test_waiver_type_reflects_faab(self, client):
        assert client._waiver_type({"isUsingAcquisitionBudget": True}) == "FAAB"
        assert client._waiver_type({"waiverOrderReset": True}) == "REVERSE_STANDINGS"
        assert client._waiver_type({}) == "ROLLING_LIST"


class TestPlayerParsing:
    def test_player_entry_is_normalised(self, client, espn_player_entry):
        record = client._parse_player_entry(espn_player_entry, "PPR", {"KC": 10})
        assert record is not None
        assert record.espn_player_id == 4362628
        assert record.name == "Test Runningback"
        assert record.position == "RB"
        assert record.pro_team == "KC"
        assert record.bye_week == 10
        assert record.adp == 6.4
        assert record.espn_rank == 5  # PPR rank, because this is a PPR league
        assert record.percent_owned == 99.8

    def test_standard_leagues_use_the_standard_rank(self, client, espn_player_entry):
        record = client._parse_player_entry(espn_player_entry, "STANDARD", {})
        assert record.espn_rank == 7

    def test_only_the_season_projection_is_used(self, client, espn_player_entry):
        record = client._parse_player_entry(espn_player_entry, "PPR", {})
        # The weekly split (70 rushing yards) must not leak in.
        assert record.raw_stats["24"] == 1180.0
        assert record.espn_projected_points == 272.4

    def test_projected_games_derived_from_total_over_average(self, client, espn_player_entry):
        record = client._parse_player_entry(espn_player_entry, "PPR", {})
        assert record.projected_games == pytest.approx(17.0, abs=0.1)

    def test_flex_eligibility_is_captured(self, client, espn_player_entry):
        record = client._parse_player_entry(espn_player_entry, "PPR", {})
        assert "FLEX" in record.eligible_slots
        assert "BE" not in record.eligible_slots

    def test_missing_projection_yields_empty_stats_not_a_crash(self, client, espn_player_entry):
        espn_player_entry["player"]["stats"] = []
        record = client._parse_player_entry(espn_player_entry, "PPR", {})
        assert record.raw_stats == {}
        assert record.espn_projected_points == 0.0

    def test_entry_without_a_name_is_skipped(self, client, espn_player_entry):
        espn_player_entry["player"]["fullName"] = ""
        assert client._parse_player_entry(espn_player_entry, "PPR", {}) is None

    def test_defense_entries_parse(self, client):
        entry = {
            "player": {
                "id": -16012,
                "fullName": "Chiefs D/ST",
                "defaultPositionId": 16,
                "proTeamId": 12,
                "eligibleSlots": [16, 20],
                "stats": [],
                "ownership": {"averageDraftPosition": 140.0},
            }
        }
        record = client._parse_player_entry(entry, "PPR", {})
        assert record.position == "DST"

    def test_free_agents_show_as_fa_not_none(self, client, espn_player_entry):
        espn_player_entry["player"]["proTeamId"] = 0
        record = client._parse_player_entry(espn_player_entry, "PPR", {})
        assert record.pro_team == "FA"

    def test_unranked_player_has_no_adp(self, client, espn_player_entry):
        espn_player_entry["player"]["ownership"] = {}
        record = client._parse_player_entry(espn_player_entry, "PPR", {})
        assert record.adp is None


class TestConstants:
    @pytest.mark.parametrize(
        "raw,expected",
        [("D/ST", "DST"), ("DST", "DST"), ("PK", "K"), ("RB", "RB"), (None, "UNKNOWN")],
    )
    def test_position_normalisation(self, raw, expected):
        assert normalise_position(raw) == expected

    def test_slot_label_normalisation(self):
        assert normalise_slot_label("RB/WR/TE") == "FLEX"
        assert normalise_slot_label("D/ST") == "DST"

    def test_stat_labels_have_a_fallback(self):
        abbrev, label = stat_label(999999)
        assert abbrev and label
