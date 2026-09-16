"""A move made on ESPN has to show up here.

Reported from real use: the user moved a player in the ESPN app and the War Room
kept showing the old lineup. Our stored roster slots only refreshed as a side
effect of the 90-minute *player pool* sweep -- and a slot move on ESPN doesn't
age the player pool at all, so nothing triggered it. The app then computed Auto
Mode moves against a roster ESPN had already changed.

Roster freshness is now its own, much shorter clock, checked in `league_dep` so
it runs before the board is built and every screen agrees with ESPN.
"""

from __future__ import annotations

from datetime import timedelta

from app.models import League, utcnow
from app.services import importer


def _league(age_seconds: float | None) -> League:
    league = League(id=1, espn_league_id=1, season=2026)
    league.imported_at = None if age_seconds is None else utcnow() - timedelta(seconds=age_seconds)
    return league


class _Settings:
    def __init__(self, connected=True):
        self.espn_swid = "{SWID}" if connected else None
        self.espn_s2 = "s2" if connected else None


def test_roster_age_is_measured_from_the_last_import():
    assert importer.roster_age_seconds(_league(None)) is None
    age = importer.roster_age_seconds(_league(120))
    assert age is not None and 110 < age < 130


def test_the_roster_clock_is_much_shorter_than_the_pool_clock():
    # The whole bug: a slot move on ESPN does not age the player pool, so tying
    # roster freshness to it meant waiting up to 90 minutes.
    assert importer.ROSTER_STALE_AFTER <= 5 * 60


def test_a_fresh_roster_is_not_re_pulled(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(5), _Settings()) is False
    assert calls == []


def test_a_stale_roster_is_re_pulled(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(600), _Settings()) is True
    assert len(calls) == 1

    # ...but not again immediately, however many screens load.
    assert importer.maybe_refresh_rosters(None, _league(600), _Settings()) is False
    assert len(calls) == 1


def test_force_ignores_both_timers(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(1), _Settings(), force=True) is True
    assert importer.maybe_refresh_rosters(None, _league(1), _Settings(), force=True) is True
    assert len(calls) == 2, "the Sync now button must never be throttled"


def test_without_cookies_nothing_is_pulled(monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "refresh_rosters", lambda *a, **k: calls.append(1) or True)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    # Demo mode and not-yet-connected accounts must not reach for ESPN.
    assert importer.maybe_refresh_rosters(None, _league(9999), _Settings(connected=False)) is False
    assert importer.maybe_refresh_rosters(
        None, _league(9999), _Settings(connected=False), force=True
    ) is False
    assert calls == []


def test_a_failed_pull_never_breaks_the_screen(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ESPN said no")

    monkeypatch.setattr(importer, "refresh_rosters", boom)
    importer._last_roster_refresh.clear()      # noqa: SLF001

    assert importer.maybe_refresh_rosters(None, _league(9999), _Settings()) is False


def test_the_week_screen_reports_its_freshness(drafted_league):
    body = drafted_league.get("/api/season/lineup").json()
    assert "espn_sync" in body
    assert "synced_at" in body["espn_sync"] and "age_seconds" in body["espn_sync"]


def test_sync_now_is_honest_when_there_is_nothing_to_sync(drafted_league):
    # Demo fixture: no ESPN cookies, so the endpoint reports that rather than
    # claiming a sync it never made.
    body = drafted_league.post("/api/season/sync").json()
    assert body["ok"] is False and body["note"]


class TestTheWeekScreenShowsIr:
    """Asked for directly: the Week tab should show IR spots the way it shows the
    bench. Two rules come with that -- a player parked on IR is not eligible to
    start, so he must not be ranked into the lineup, and a healed one left there
    blocks every other roster move, so the screen has to say so.
    """

    def test_the_payload_carries_the_ir_section(self, drafted_league):
        body = drafted_league.get("/api/season/lineup").json()
        assert "ir" in body
        ir = body["ir"]
        assert set(ir) == {"slots", "used", "open", "players", "must_return"}
        assert ir["open"] == max(ir["slots"] - ir["used"], 0)

    def test_a_player_on_ir_is_never_ranked_into_the_lineup(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam

        # Park my highest-projected player on IR behind the app's back, the way a
        # move in the ESPN app would.
        with session_scope() as s:
            league = s.query(League).first()
            mine = s.query(LeagueTeam).filter(LeagueTeam.league_id == league.id, LeagueTeam.is_mine).first()
            roster = list(mine.roster or [])
            assert roster, "fixture should have a roster"
            stashed = roster[0]["espn_player_id"]
            roster[0] = {**roster[0], "slot": "IR"}
            mine.roster = roster

        body = drafted_league.get("/api/season/lineup").json()
        started = {
            s["player"]["espn_player_id"] for s in body["starters"] if s["player"]
        }
        benched = {p["espn_player_id"] for p in body["bench"]}
        on_ir = {p["espn_player_id"] for p in body["ir"]["players"]}

        assert stashed in on_ir, "he should show up under injured reserve"
        assert stashed not in started, "a player on IR is not eligible to start"
        assert stashed not in benched, "and he is not on the bench either"
        assert body["ir"]["used"] == 1


class TestGettingOffIrFromInsideTheApp:
    """ESPN forces a healed player off IR and blocks every other roster move until
    he is off it. Doing that should not require opening the ESPN app.

    The irreversible half stays gated: with a full roster nothing happens until the
    user names the drop themselves.
    """

    def _stash(self, client) -> int:
        from app.db import session_scope
        from app.models import League, LeagueTeam

        with session_scope() as s:
            league = s.query(League).first()
            mine = s.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id, LeagueTeam.is_mine
            ).first()
            roster = list(mine.roster or [])
            stashed = roster[0]["espn_player_id"]
            roster[0] = {**roster[0], "slot": "IR"}
            mine.roster = roster
        return stashed

    def test_the_install_switch_still_gates_it(self, drafted_league):
        pid = self._stash(drafted_league)
        r = drafted_league.post("/api/season/ir/return", json={"espn_player_id": pid})
        # Auto Mode is off by default, so this write is refused like every other.
        assert r.status_code in (403, 404)

    def test_a_player_not_on_ir_is_refused(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam, User

        assert drafted_league.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200
        with session_scope() as s:
            s.query(User).filter(User.username == "tester").first().can_auto_mode = True
            league = s.query(League).first()
            mine = s.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id, LeagueTeam.is_mine
            ).first()
            benched = mine.roster[0]["espn_player_id"]

        r = drafted_league.post("/api/season/ir/return", json={"espn_player_id": benched})
        # No cookies in the demo fixture, so it stops at the credential gate --
        # never at "sure, moved him".
        assert r.status_code == 409
        assert "Connect ESPN" in r.json()["detail"]

    def test_a_drop_only_transaction_carries_no_add(self):
        from app.espn import waiver_write

        body = waiver_write.build_drop_body(
            team_id=7, swid="{SWID}", scoring_period_id=2,
            drop=waiver_write.WaiverPlayer(99, "Spare Part", "RB"),
        )
        assert [i["type"] for i in body["items"]] == ["DROP"]
        assert body["items"][0]["toTeamId"] == 0, "dropped players go to nobody"
        assert body["items"][0]["fromTeamId"] == 7
        assert "bidAmount" not in body, "a drop spends no FAAB"


def test_the_week_screen_is_told_the_exact_writes(drafted_league):
    """The screen must not infer the diff from its own tables.

    It did, and it drifted: two starters swapping equivalent slots showed as two
    pending changes while the write path (correctly) declined to make them. The
    payload now carries the moves computed by the same functions that send them.
    """
    body = drafted_league.get("/api/season/lineup").json()
    assert "pending_moves" in body
    for move in body["pending_moves"]:
        assert set(move) == {"espn_player_id", "name", "from_slot", "to_slot"}
        assert move["from_slot"] != move["to_slot"], "a move must actually move someone"
