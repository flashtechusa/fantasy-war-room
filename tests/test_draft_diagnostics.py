"""ESPN Draft Sync Diagnostics, and the fallback arbitration it reports on.

Two things are being pinned:

* `live_draft_picks()` takes whichever source reports more picks, so adding the
  direct `mDraftDetail` read can only ever *add* picks -- never lose one that
  the library path was already returning.
* the diagnostics report is built from counts and timings only. No cookie, no
  header and no ESPN payload may appear in it, because it is meant to be safe
  to leave open on a screen during a draft.
"""

from __future__ import annotations

import pytest

from app.espn.client import EspnClient, EspnConnectionError, LiveDraftResult
from app.espn.draft_feed import DraftFeedSnapshot
from app.services.draft_diag import DraftDiagnostics, SyncAttempt

SWID = "{1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D}"
S2 = "AEB" + "x9Kq2Lm" * 30


def make_client(monkeypatch, library, direct, draft_source="auto"):
    """An `EspnClient` with both draft sources stubbed.

    `library` / `direct` are a list of picks, or an exception to raise.
    """
    client = EspnClient(1, 2026, swid=SWID, espn_s2=S2, draft_source=draft_source)

    def library_picks(self):
        if isinstance(library, Exception):
            raise library
        return library

    def snapshot(self, player_names=None):
        if isinstance(direct, Exception):
            raise direct
        return DraftFeedSnapshot(
            picks=direct,
            in_progress=True,
            latency_ms=42.0,
            endpoint="https://lm-api-reads.fantasy.espn.com/…/leagues/1?view=mDraftDetail",
        )

    monkeypatch.setattr(EspnClient, "_library_draft_picks", library_picks)
    monkeypatch.setattr(EspnClient, "draft_snapshot", snapshot)
    return client


def picks(count: int, source: str = "x") -> list[dict]:
    return [
        {"overall_pick": n, "espn_player_id": 1000 + n, "player_name": f"{source}{n}"}
        for n in range(1, count + 1)
    ]


class TestSourceArbitration:
    def test_the_direct_read_wins_during_a_live_draft(self, monkeypatch):
        """The library returns nothing mid-draft; that must not be the answer."""
        client = make_client(monkeypatch, library=[], direct=picks(7))
        result = client.live_draft_picks()
        assert len(result) == 7
        assert result.source == "espn_draft_detail"
        assert result.in_progress is True

    def test_the_library_wins_when_it_has_more(self, monkeypatch):
        client = make_client(monkeypatch, library=picks(120), direct=picks(3))
        result = client.live_draft_picks()
        assert len(result) == 120
        assert result.source == "espn_api"

    def test_a_broken_direct_read_falls_back_without_raising(self, monkeypatch):
        client = make_client(
            monkeypatch,
            library=picks(5),
            direct=EspnConnectionError("mDraftDetail exploded"),
        )
        result = client.live_draft_picks()
        assert len(result) == 5
        assert result.source == "espn_api"
        assert any("mDraftDetail" in error for error in result.errors)

    def test_a_broken_library_falls_forward(self, monkeypatch):
        client = make_client(
            monkeypatch, library=EspnConnectionError("library exploded"), direct=picks(4)
        )
        result = client.live_draft_picks()
        assert len(result) == 4
        assert result.source == "espn_draft_detail"

    def test_both_failing_surfaces_the_error(self, monkeypatch):
        client = make_client(
            monkeypatch,
            library=EspnConnectionError("library down"),
            direct=EspnConnectionError("endpoint down"),
        )
        with pytest.raises(EspnConnectionError) as excinfo:
            client.live_draft_picks()
        assert "library down" in str(excinfo.value)
        assert "endpoint down" in str(excinfo.value)

    def test_both_empty_is_a_pre_draft_league_not_an_error(self, monkeypatch):
        client = make_client(monkeypatch, library=[], direct=[])
        result = client.live_draft_picks()
        assert len(result) == 0
        assert result.errors == []

    def test_pinning_to_the_library_skips_the_new_path_entirely(self, monkeypatch):
        """The escape hatch: exactly the behaviour from before the fallback."""
        called = {"direct": False}

        def snapshot(self, player_names=None):
            called["direct"] = True
            return DraftFeedSnapshot(picks=picks(9))

        client = make_client(monkeypatch, library=picks(2), direct=[], draft_source="espn_api")
        monkeypatch.setattr(EspnClient, "draft_snapshot", snapshot)
        result = client.live_draft_picks()
        assert called["direct"] is False
        assert len(result) == 2

    def test_pinning_to_direct_propagates_its_failure(self, monkeypatch):
        client = make_client(
            monkeypatch,
            library=picks(50),
            direct=EspnConnectionError("endpoint down"),
            draft_source="direct",
        )
        with pytest.raises(EspnConnectionError, match="endpoint down"):
            client.live_draft_picks()

    def test_an_unknown_source_setting_falls_back_to_auto(self):
        from app.config import Settings

        assert Settings(espn_draft_source="nonsense").espn_draft_source == "auto"

    def test_a_pre_draft_league_survives_the_new_endpoint_failing(self, monkeypatch):
        """The regression a fallback must not introduce.

        Before the direct read existed, a league whose draft had not happened
        returned an empty list. It still must -- a bad day for the new endpoint
        cannot turn that into an error.
        """
        client = make_client(
            monkeypatch, library=[], direct=EspnConnectionError("endpoint down")
        )
        result = client.live_draft_picks()
        assert len(result) == 0
        assert result.source == "espn_api"
        assert any("mDraftDetail" in error for error in result.errors)


class TestLiveDraftResult:
    def test_it_still_behaves_like_the_list_it_replaced(self):
        result = LiveDraftResult(picks=picks(3))
        assert len(result) == 3
        assert result[0]["overall_pick"] == 1
        assert [p["overall_pick"] for p in result] == [1, 2, 3]
        assert result.latest_pick_number == 3

    def test_an_empty_result_is_falsy_by_length(self):
        assert len(LiveDraftResult()) == 0
        assert LiveDraftResult().latest_pick_number == 0


class TestDiagnosticsStore:
    def test_an_empty_report_is_still_a_complete_shape(self):
        report = DraftDiagnostics().report(1, poll_interval=10, sync_enabled=False)
        assert report["picks"]["espn_latest_pick"] == 0
        assert report["response"]["last_success_at"] is None
        assert report["response"]["success_rate"] is None
        assert report["last_error"] is None
        assert report["recent"] == []

    def test_a_successful_attempt_is_summarised(self):
        store = DraftDiagnostics()
        store.record(
            1,
            SyncAttempt(
                ok=True,
                source="espn_draft_detail",
                endpoint="https://lm-api-reads.fantasy.espn.com/…?view=mDraftDetail",
                latency_ms=120.0,
                espn_pick_count=24,
                espn_latest_pick=24,
                local_pick_count=24,
                local_latest_pick=24,
                new_picks=3,
            ),
        )
        report = store.report(1, poll_interval=10, sync_enabled=True)
        assert report["picks"]["espn_latest_pick"] == 24
        assert report["picks"]["new_picks_detected"] is True
        assert report["picks"]["behind_by"] == 0
        assert report["response"]["last_latency_ms"] == 120.0
        assert report["response"]["success_rate"] == 1.0
        assert report["endpoint"]["source"] == "espn_draft_detail"

    def test_falling_behind_is_visible(self):
        store = DraftDiagnostics()
        store.record(
            1, SyncAttempt(ok=True, espn_latest_pick=31, local_latest_pick=24)
        )
        assert store.report(1, poll_interval=10, sync_enabled=True)["picks"]["behind_by"] == 7

    def test_a_failure_is_recorded_and_reported(self):
        store = DraftDiagnostics()
        store.record(1, SyncAttempt(ok=False, error="ESPN timed out"))
        report = store.report(1, poll_interval=10, sync_enabled=True)
        assert report["last_error"]["detail"] == "ESPN timed out"
        assert report["response"]["success_rate"] == 0.0

    def test_errors_are_redacted_on_the_way_in(self):
        """A stack trace can carry a request URL. It must not reach the screen."""
        store = DraftDiagnostics()
        store.record(1, SyncAttempt(ok=False, error=f"denied for SWID={SWID} s2={S2}"))
        report = store.report(1, poll_interval=10, sync_enabled=True)
        assert SWID not in str(report)
        assert S2 not in str(report)

    def test_endpoints_are_redacted_too(self):
        store = DraftDiagnostics()
        store.record(1, SyncAttempt(ok=True, endpoint=f"https://fan.api.espn.com/v2/fans/{SWID}"))
        assert SWID not in str(store.report(1, poll_interval=10, sync_enabled=True))

    def test_history_is_bounded(self):
        store = DraftDiagnostics()
        for n in range(60):
            store.record(1, SyncAttempt(ok=True, espn_latest_pick=n))
        report = store.report(1, poll_interval=10, sync_enabled=True)
        assert report["polling"]["attempts_recorded"] == report["polling"]["history_limit"]
        assert len(report["recent"]) == 10
        # The newest attempt is the one reported.
        assert report["picks"]["espn_latest_pick"] == 59

    def test_drafts_do_not_share_history(self):
        store = DraftDiagnostics()
        store.record(1, SyncAttempt(ok=True, espn_latest_pick=5))
        assert store.report(2, poll_interval=10, sync_enabled=True)["picks"][
            "espn_latest_pick"
        ] == 0

    def test_clearing_works(self):
        store = DraftDiagnostics()
        store.record(1, SyncAttempt(ok=True))
        store.clear(1)
        assert store.report(1, poll_interval=10, sync_enabled=True)["recent"] == []

    def test_latency_statistics_come_from_successes_only(self):
        store = DraftDiagnostics()
        store.record(1, SyncAttempt(ok=True, latency_ms=100.0))
        store.record(1, SyncAttempt(ok=True, latency_ms=300.0))
        store.record(1, SyncAttempt(ok=False, latency_ms=0.0, error="boom"))
        response = store.report(1, poll_interval=10, sync_enabled=True)["response"]
        assert response["average_latency_ms"] == 200.0
        assert response["max_latency_ms"] == 300.0


class TestDiagnosticsEndpoint:
    def test_it_reports_a_complete_shape_before_any_sync(self, client):
        client.post("/api/league/import")
        body = client.get("/api/draft/diagnostics").json()
        assert body["polling"]["interval_seconds"] > 0
        assert body["picks"]["local_pick_count"] == 0
        assert body["config"]["draft_source"] == "auto"
        assert len(body["endpoint"]["candidates"]) == 2

    def test_local_picks_are_counted_by_source(self, client):
        client.post("/api/league/import")
        board = client.get("/api/players?limit=3").json()
        for row in board["players"][:2]:
            client.post("/api/draft/pick", json={"espn_player_id": row["espn_player_id"]})
        body = client.get("/api/draft/diagnostics").json()
        assert body["local"]["picks_recorded"] == 2
        assert body["local"]["picks_manual"] == 2
        assert body["local"]["picks_from_espn"] == 0
        assert body["picks"]["local_latest_pick"] == 0  # nothing synced yet

    def test_it_exposes_no_credentials(self, client):
        client.post("/api/league/import")
        text = client.get("/api/draft/diagnostics").text.lower()
        assert "cookie" not in text
        assert "espn_s2" not in text
        assert "swid" not in text

    def test_it_can_be_cleared(self, client):
        client.post("/api/league/import")
        assert client.delete("/api/draft/diagnostics").json()["cleared"] is True

    def test_it_requires_a_session(self, anon_client):
        assert anon_client.get("/api/draft/diagnostics").status_code == 401


class TestSyncRecordsDiagnostics:
    @pytest.fixture(autouse=True)
    def _reset_poll_gate(self):
        """The poll-interval gate is process-global and keyed on draft id.

        Every test gets draft id 1, so without this the second test in the
        class is rate-limited by the first one's sync.
        """
        from app.api import routes_draft

        routes_draft._last_sync.clear()
        yield
        routes_draft._last_sync.clear()

    def test_a_failed_sync_is_recorded_before_the_error_propagates(self, client, monkeypatch):
        """During a draft, "ESPN stopped answering" is the useful signal."""
        client.post("/api/league/import")

        from app.services import draft as draft_service

        def explode(settings=None):
            raise EspnConnectionError("ESPN is unreachable")

        monkeypatch.setattr(draft_service, "build_espn_client", explode)
        assert client.post("/api/draft/sync").status_code == 502

        body = client.get("/api/draft/diagnostics").json()
        assert body["last_error"]["detail"] == "ESPN is unreachable"
        assert body["response"]["success_rate"] == 0.0

    def test_a_successful_sync_records_source_and_counts(self, client, monkeypatch):
        client.post("/api/league/import")
        board = client.get("/api/players?limit=4").json()
        ids = [row["espn_player_id"] for row in board["players"][:3]]

        from app.services import draft as draft_service

        class FakeClient:
            def live_draft_picks(self, player_names=None):
                return LiveDraftResult(
                    picks=[
                        {"espn_player_id": pid, "overall_pick": index + 1}
                        for index, pid in enumerate(ids)
                    ],
                    source="espn_draft_detail",
                    endpoint="https://lm-api-reads.fantasy.espn.com/…?view=mDraftDetail",
                    latency_ms=88.0,
                    in_progress=True,
                    direct_pick_count=3,
                    library_pick_count=0,
                )

        monkeypatch.setattr(draft_service, "build_espn_client", lambda settings=None: FakeClient())

        synced = client.post("/api/draft/sync").json()
        assert synced["added"] == 3
        assert synced["source"] == "espn_draft_detail"
        # The Live Draft screen uses these to auto-follow a running draft.
        assert synced["in_progress"] is True
        assert synced["total_espn_picks"] == 3

        body = client.get("/api/draft/diagnostics").json()
        assert body["picks"]["espn_latest_pick"] == 3
        assert body["picks"]["local_latest_pick"] == 3
        assert body["picks"]["new_picks_last_sync"] == 3
        assert body["picks"]["espn_draft_in_progress"] is True
        assert body["picks"]["direct_pick_count"] == 3
        assert body["response"]["last_latency_ms"] == 88.0
        assert body["local"]["picks_from_espn"] == 3

    def test_a_second_sync_adds_nothing_and_says_so(self, client, monkeypatch):
        """The regression guard for the library's duplicate-on-refresh bug."""
        client.post("/api/league/import")
        board = client.get("/api/players?limit=3").json()
        ids = [row["espn_player_id"] for row in board["players"][:2]]

        from app.services import draft as draft_service
        from app.api import routes_draft

        class FakeClient:
            def live_draft_picks(self, player_names=None):
                return LiveDraftResult(
                    picks=[
                        {"espn_player_id": pid, "overall_pick": index + 1}
                        for index, pid in enumerate(ids)
                    ],
                    source="espn_draft_detail",
                )

        monkeypatch.setattr(draft_service, "build_espn_client", lambda settings=None: FakeClient())
        assert client.post("/api/draft/sync").json()["added"] == 2

        routes_draft._last_sync.clear()  # skip the poll-interval gate
        second = client.post("/api/draft/sync").json()
        assert second["added"] == 0
        assert second["total_espn_picks"] == 2

        body = client.get("/api/draft/diagnostics").json()
        assert body["picks"]["new_picks_detected"] is False
        assert body["local"]["picks_recorded"] == 2

