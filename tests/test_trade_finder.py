"""The Trade Finder: proposing trades, not just grading one.

The properties under test are the ones that make a proposal trustworthy:
- a mutual trade (both starting lineups improve) is found when one exists;
- a trade that only helps you is bucketed as a *longshot*, never as mutual;
- nothing that opens a starting hole is ever proposed;
- the horizon (this week vs rest of season) actually drives the result.
"""

from __future__ import annotations

from app.engine.league_shape import LeagueShape
from app.engine.trades import NOISE_FLOOR, find_trades
from app.engine.weekly import WeeklyPlayer

SHAPE = LeagueShape.from_slots(
    team_count=12,
    roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1},
    bench_slots=5,
    ir_slots=0,
)


def P(pid, name, pos, season, week=None):
    return WeeklyPlayer(
        espn_player_id=pid, name=name, position=pos,
        season_points=season, week_points=week if week is not None else season / 17.0,
    )


def _rb_heavy(base):
    # Four startable RBs (the 4th is pure bench surplus), weak WRs.
    return [
        P(base + 1, "QB", "QB", 300), P(base + 2, "RB1", "RB", 250),
        P(base + 3, "RB2", "RB", 245), P(base + 4, "RB3", "RB", 240),
        P(base + 5, "RB4", "RB", 235), P(base + 6, "WRa", "WR", 100),
        P(base + 7, "WRb", "WR", 95), P(base + 8, "TE", "TE", 150),
    ]


def _wr_heavy(base):
    return [
        P(base + 1, "QB", "QB", 300), P(base + 2, "WR1", "WR", 250),
        P(base + 3, "WR2", "WR", 245), P(base + 4, "WR3", "WR", 240),
        P(base + 5, "WR4", "WR", 235), P(base + 6, "RBa", "RB", 100),
        P(base + 7, "RBb", "RB", 95), P(base + 8, "TE", "TE", 150),
    ]


def test_a_mutual_surplus_swap_is_found():
    mine, theirs = _rb_heavy(0), _wr_heavy(100)
    res = find_trades(mine, [(2, "Rival", theirs)], SHAPE, week=1, horizon="season")
    assert res["mutual"], "a complementary RB-surplus / WR-surplus swap should be found"
    top = res["mutual"][0]
    # Both starting lineups genuinely improve.
    assert top.my_delta > NOISE_FLOOR
    assert top.their_delta > NOISE_FLOOR
    assert top.kind == "mutual"
    # I give a running back and get a wide receiver (the surplus↔need swap).
    assert any(p.position == "RB" for p in top.give)
    assert any(p.position == "WR" for p in top.receive)


def test_every_proposal_has_both_sides_improving_when_mutual():
    res = find_trades(_rb_heavy(0), [(2, "Rival", _wr_heavy(100))], SHAPE, week=1)
    for p in res["mutual"]:
        assert p.my_delta > NOISE_FLOOR and p.their_delta > NOISE_FLOOR


def test_a_lopsided_trade_is_a_longshot_not_mutual():
    # I badly need a WR; they have a WR4 that never starts for them (pure bench),
    # so giving it costs them nothing while it transforms my lineup -- a trade
    # that helps me and barely moves them: a longshot, not mutual.
    mine = [
        P(1, "QB", "QB", 300), P(2, "RB1", "RB", 240), P(3, "RB2", "RB", 235),
        P(4, "WRa", "WR", 80), P(5, "WRb", "WR", 75), P(6, "TE", "TE", 150),
        P(7, "junkRB", "RB", 50),
    ]
    theirs = [
        P(11, "QB", "QB", 300), P(12, "WR1", "WR", 250), P(13, "WR2", "WR", 245),
        P(14, "WR3", "WR", 240), P(15, "WR4", "WR", 200), P(16, "RB1", "RB", 230),
        P(17, "RB2", "RB", 225), P(18, "TE", "TE", 150),
    ]
    res = find_trades(mine, [(2, "Rival", theirs)], SHAPE, week=1, horizon="season")
    assert res["longshots"], "a helps-me / neutral-for-them trade should be a longshot"
    for p in res["longshots"]:
        assert p.my_delta > NOISE_FLOOR
        assert p.their_delta <= NOISE_FLOOR  # by definition not mutual
        assert p.kind == "longshot"
    # And none of these lopsided ones masquerade as mutual.
    for p in res["mutual"]:
        assert p.their_delta > NOISE_FLOOR


def test_longshots_can_be_suppressed():
    mine = [
        P(1, "QB", "QB", 300), P(2, "RB1", "RB", 240), P(3, "RB2", "RB", 235),
        P(4, "WRa", "WR", 80), P(5, "WRb", "WR", 75), P(6, "TE", "TE", 150),
        P(7, "junkRB", "RB", 50),
    ]
    theirs = [
        P(11, "QB", "QB", 300), P(12, "WR1", "WR", 250), P(13, "WR2", "WR", 245),
        P(14, "WR3", "WR", 240), P(15, "WR4", "WR", 200), P(16, "RB1", "RB", 230),
        P(17, "RB2", "RB", 225), P(18, "TE", "TE", 150),
    ]
    res = find_trades(
        mine, [(2, "Rival", theirs)], SHAPE, week=1, include_longshots=False
    )
    assert res["longshots"] == []


def test_never_proposes_a_trade_that_opens_a_starting_hole():
    # Give-side thin: only two RBs, both starters. Any trade sending an RB away
    # for a non-RB would drop below the RB requirement and must be refused.
    mine = [
        P(1, "QB", "QB", 300), P(2, "RB1", "RB", 240), P(3, "RB2", "RB", 230),
        P(4, "WR1", "WR", 90), P(5, "WR2", "WR", 85), P(6, "TE", "TE", 150),
    ]
    theirs = _wr_heavy(100)
    res = find_trades(mine, [(2, "Rival", theirs)], SHAPE, week=1)
    for p in res["mutual"] + res["longshots"]:
        rb_out = sum(1 for x in p.give if x.position == "RB")
        rb_in = sum(1 for x in p.receive if x.position == "RB")
        # 2 RB starters minus any given, plus any received, must still fill 2+FLEX cover.
        assert 2 - rb_out + rb_in >= 2, f"{p.headline} would leave an RB hole"
        assert not any("unfilled" in n.lower() for n in p.notes)


def test_horizon_drives_the_result():
    # Season-complementary rosters, but identical (flat) weekly points -- so a
    # rest-of-season search finds mutual swaps while a this-week search finds
    # none, because no swap changes anyone's week.
    mine = [P(p.espn_player_id, p.name, p.position, p.season_points, week=10.0) for p in _rb_heavy(0)]
    theirs = [P(p.espn_player_id, p.name, p.position, p.season_points, week=10.0) for p in _wr_heavy(100)]

    season = find_trades(mine, [(2, "Rival", theirs)], SHAPE, week=1, horizon="season")
    week = find_trades(mine, [(2, "Rival", theirs)], SHAPE, week=1, horizon="week")

    assert season["mutual"], "rest-of-season should find the complementary swap"
    assert not week["mutual"], "flat weekly points => no this-week upgrade to find"
    assert season["horizon"] == "season" and week["horizon"] == "week"


# --- API wiring: assembly, serialization, params ---------------------------


class TestEndpoint:
    def test_returns_the_proposal_shape(self, drafted_league):
        body = drafted_league.get("/api/season/trade-finder").json()
        assert body["horizon"] == "season"
        assert isinstance(body["week"], int)
        assert isinstance(body["mutual"], list)
        assert isinstance(body["longshots"], list)
        # Rosters exist (dealt by the fixture), so no "nothing to work with".
        assert body["reason"] is None
        # Every proposal carries both-side deltas and player lists.
        for p in body["mutual"] + body["longshots"]:
            assert p["give"] and p["receive"]
            assert "my_delta" in p and "their_delta" in p
            assert p["their_team_id"] and p["their_label"]

    def test_week_horizon_is_respected(self, drafted_league):
        body = drafted_league.get("/api/season/trade-finder?horizon=week").json()
        assert body["horizon"] == "week"

    def test_longshots_can_be_turned_off(self, drafted_league):
        body = drafted_league.get(
            "/api/season/trade-finder?include_longshots=false"
        ).json()
        assert body["longshots"] == []

    def test_reports_when_there_is_no_roster_yet(self, client):
        # Pre-draft demo league: no rosters, no draft picks -> nothing to do.
        client.post("/api/league/import")
        body = client.get("/api/season/trade-finder").json()
        assert body["reason"] == "no_my_roster"
        assert body["mutual"] == [] and body["longshots"] == []

    def test_never_proposes_a_trade_with_my_own_team(self, drafted_league):
        body = drafted_league.get("/api/season/trade-finder").json()
        my_status = drafted_league.get("/api/espn/status").json()
        my_team_id = my_status.get("my_team_id")
        for p in body["mutual"] + body["longshots"]:
            assert p["their_team_id"] != my_team_id
