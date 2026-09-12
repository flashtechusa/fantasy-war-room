"""Auto Mode -- staged and gated three ways.

The properties that keep autopilot safe:
- Off by default: no plan runs until the install switch, the per-account
  capability, and the user's own opt-in all line up.
- Lineup writing is live (reversible, own-team-only) and user-triggered; waivers
  and trades stay dry-run until each payload is captured.
- The lineup planner produces real start/sit moves from the optimal lineup.
- Only the owner flips the install switch or grants the capability.
"""

from __future__ import annotations

from app.services import automode


def _tester_id(client) -> int:
    users = client.get("/api/admin/users").json()["users"]
    return next(u["id"] for u in users if u["username"] == "tester")


def _grant_auto_mode(client):
    """Owner grants themselves the capability and turns the install switch on."""
    assert client.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200
    uid = _tester_id(client)
    r = client.patch(f"/api/admin/users/{uid}", json={"can_auto_mode": True})
    assert r.status_code == 200 and r.json()["user"]["can_auto_mode"] is True


# --- write flags: lineup + waivers live, trades never auto-execute ---------


def test_lineup_and_waivers_write_trades_never_auto_execute():
    # Lineup (reversible) and waiver add/drop (guarded by a preview + confirm)
    # both write. Trades are never fired autonomously -- only surfaced for the
    # user's one-tap approval.
    assert automode.LINEUP_WRITE_ENABLED is True
    assert automode.WAIVER_WRITE_ENABLED is True
    assert automode.TRADE_AUTO_EXECUTE is False


def test_is_active_needs_all_three():
    assert automode.is_active(install_on=True, capable=True, user_on=True) is True
    assert automode.is_active(install_on=False, capable=True, user_on=True) is False
    assert automode.is_active(install_on=True, capable=False, user_on=True) is False
    assert automode.is_active(install_on=True, capable=True, user_on=False) is False


# --- gating + planning through the API -------------------------------------


def test_auto_mode_is_off_by_default(drafted_league):
    body = drafted_league.get("/api/season/automode").json()
    assert body["gates"] == {"install_enabled": False, "capable": False, "user_enabled": False}
    assert body["plan"]["active"] is False
    assert body["plan"]["reason"]


def test_settings_opt_in_requires_capability(drafted_league):
    # Turning on auto_mode without the granted capability is refused.
    resp = drafted_league.post("/api/season/automode/settings", json={"auto_mode": True})
    assert resp.status_code == 403


def test_lineup_plan_is_computed_and_ready_to_apply_when_active(drafted_league):
    _grant_auto_mode(drafted_league)
    ok = drafted_league.post(
        "/api/season/automode/settings", json={"auto_mode": True, "auto_lineup": True}
    )
    assert ok.status_code == 200 and ok.json()["auto_mode"] is True

    body = drafted_league.get("/api/season/automode").json()
    assert body["plan"]["active"] is True
    # The GET plan itself writes nothing -- the write happens on the Apply action.
    assert body["plan"]["dry_run"] is True
    lineup = body["plan"]["lineup"]
    assert lineup is not None
    # Lineup writing is live: the plan is ready to apply on demand.
    assert lineup["write_enabled"] is True
    assert lineup["status"] == "ready_to_apply"
    # The fixture benches everyone, so the plan wants to start the optimal set.
    assert lineup["start"] or lineup["already_optimal"]

    # And the cycle was logged to the activity trail.
    activity = body["activity"]
    assert any(a["tier"] == "lineup" for a in activity)


def test_admin_switch_and_capability_are_owner_only(drafted_league):
    from app.db import session_scope
    from app.models import User

    # Works as owner.
    assert drafted_league.post("/api/admin/auto-mode", json={"enabled": True}).status_code == 200

    with session_scope() as s:
        s.query(User).filter(User.username == "tester").first().role = "client"

    assert drafted_league.post("/api/admin/auto-mode", json={"enabled": False}).status_code == 403
    assert drafted_league.get("/api/admin/auto-mode").status_code == 403


class TestTheLineupReadsTheWeek:
    """Reported from real use: an OUT starter (Brock Bowers) stayed in the lineup
    and Auto Mode logged "Lineup already optimal -- no change" every cycle, while
    the Week screen correctly benched him.

    Cause: Auto Mode optimised on `engine.roster_players`, which carries the
    SEASON projection. An elite player who is OUT this week still ranked first, so
    the "optimal" lineup matched ESPN and the diff was empty. The lineup must be
    scored for the week being set.
    """

    def test_an_out_star_is_benched_not_started(self):
        from app.engine.roster import build_optimal_lineup
        from app.engine.league_shape import LeagueShape
        from app.engine.weekly import WeeklyPlayer

        shape = LeagueShape(team_count=10, dedicated={"TE": 1}, bench_slots=1)

        # A stud TE who is OUT this week, and a modest healthy TE.
        stud = WeeklyPlayer(
            espn_player_id=1, name="Bowers", position="TE",
            week_points=0.0, season_points=300.0, injury_status="OUT", week=1,
        )
        backup = WeeklyPlayer(
            espn_player_id=2, name="Backup TE", position="TE",
            week_points=8.0, season_points=80.0, week=1,
        )

        # Season basis (the bug): the OUT stud wins the TE slot.
        season_lineup = build_optimal_lineup(
            [p.as_roster_player(use_week=False) for p in (stud, backup)], shape
        )
        assert season_lineup.starters[0].player.name == "Bowers"

        # Week basis (the fix): the healthy player starts instead.
        week_lineup = build_optimal_lineup(
            [p.as_roster_player(use_week=True) for p in (stud, backup)], shape
        )
        assert week_lineup.starters[0].player.name == "Backup TE"

    def test_lineup_moves_benches_the_out_player(self):
        from app.engine.league_shape import LeagueShape
        from app.engine.weekly import WeeklyPlayer
        from app.services import automode

        shape = LeagueShape(team_count=10, dedicated={"TE": 1}, bench_slots=1)
        stud = WeeklyPlayer(
            espn_player_id=1, name="Bowers", position="TE",
            week_points=0.0, season_points=300.0, injury_status="OUT", week=1,
        )
        backup = WeeklyPlayer(
            espn_player_id=2, name="Backup TE", position="TE",
            week_points=8.0, season_points=80.0, week=1,
        )
        roster = [p.as_roster_player(use_week=True) for p in (stud, backup)]

        # ESPN currently has the OUT stud starting and the healthy TE benched.
        moves = automode.lineup_moves(roster, shape, {1: "TE", 2: "BE"})
        by_id = {m.espn_player_id: m for m in moves}
        assert by_id[1].to_slot == "BE", "the OUT player must be benched"
        assert by_id[2].to_slot == "TE", "the healthy player must start"


class TestInjuredReserve:
    """Two IR spots are free bench room -- until a player heals.

    Reported from real use: a hurt starter (Brock Bowers, OUT) sat on the bench
    taking up an active roster spot the user needed to add a player. ESPN lets him
    sit in an IR slot instead, at no cost to the active limit -- but the moment his
    tag clears, ESPN blocks *every* other roster move until he is off IR.

    The rules these tests pin down:
    - a still-hurt player on IR is never pulled back off it;
    - a hurt player taking an active spot is stashed;
    - a healed player with bench room comes back;
    - a healed player with NO bench room is reported, not dropped (option b);
    - the stash is a second ESPN transaction, so a league that refuses it still
      gets its lineup set.
    """

    @staticmethod
    def _roster(*specs):
        """(id, name, position, week_points, injury_status) -> roster players."""
        from app.engine.weekly import WeeklyPlayer

        return [
            WeeklyPlayer(
                espn_player_id=pid, name=name, position=pos,
                week_points=pts, season_points=pts * 16, injury_status=inj, week=1,
            ).as_roster_player(use_week=True)
            for pid, name, pos, pts, inj in specs
        ]

    class _League:
        def __init__(self, ir_slots, bench_slots=2, roster_slots=None):
            self.ir_slots = ir_slots
            self.bench_slots = bench_slots
            self.roster_slots = roster_slots or {"TE": 1}

    def test_ir_eligibility_follows_the_tag(self):
        assert automode.ir_eligible("OUT") is True
        assert automode.ir_eligible("injury_reserve") is True
        assert automode.ir_eligible("QUESTIONABLE") is False
        assert automode.ir_eligible(None) is False

    def test_a_hurt_bench_player_is_stashed_and_frees_a_spot(self):
        roster = self._roster(
            (1, "Starter TE", "TE", 10.0, None),
            (2, "Hurt Guy", "TE", 0.0, "OUT"),
        )
        plan = automode.ir_plan_for(
            roster, {1: "TE", 2: "BE"}, self._League(ir_slots=2, bench_slots=1)
        )
        assert [name for _, name in plan.to_ir] == ["Hurt Guy"]
        assert plan.blocked == [] and plan.needs_attention is False

    def test_a_still_hurt_player_is_left_on_ir(self):
        roster = self._roster(
            (1, "Starter TE", "TE", 10.0, None),
            (2, "Hurt Guy", "TE", 0.0, "OUT"),
        )
        current = {1: "TE", 2: "IR"}
        plan = automode.ir_plan_for(roster, current, self._League(ir_slots=2))
        assert plan.stay_in_ir == {2}
        assert plan.from_ir == [] and plan.to_ir == []

        # And no move yanks him off IR -- the bug this pinning prevents.
        moves, stash = automode.lineup_and_ir_moves(
            roster, _shape(), current, plan
        )
        assert [m.espn_player_id for m in moves] == []
        assert stash == []

    def test_a_healed_player_comes_back_when_there_is_room(self):
        roster = self._roster(
            (1, "Starter TE", "TE", 10.0, None),
            (2, "Healed Guy", "TE", 6.0, None),
        )
        plan = automode.ir_plan_for(
            roster, {1: "TE", 2: "IR"}, self._League(ir_slots=2, bench_slots=2)
        )
        assert [name for _, name in plan.from_ir] == ["Healed Guy"]
        assert plan.blocked == []

    def test_a_healed_player_with_a_full_bench_is_reported_not_dropped(self):
        # bench_slots=0 and the one active slot already taken: no room for him.
        roster = self._roster(
            (1, "Starter TE", "TE", 10.0, None),
            (2, "Healed Guy", "TE", 6.0, None),
        )
        current = {1: "TE", 2: "IR"}
        plan = automode.ir_plan_for(
            roster, current, self._League(ir_slots=2, bench_slots=0)
        )
        assert [name for _, name in plan.blocked] == ["Healed Guy"]
        assert plan.needs_attention is True
        assert plan.from_ir == []

        # He stays pinned to IR: we must not compute a move ESPN will not allow.
        moves, stash = automode.lineup_and_ir_moves(roster, _shape(), current, plan)
        assert all(m.espn_player_id != 2 for m in moves)
        assert stash == []
        assert "Healed Guy" in automode.ir_blocked_message(plan)

    def test_the_drop_choice_only_names_the_player(self):
        roster = self._roster(
            (1, "Starter TE", "TE", 10.0, None),
            (2, "Healed Guy", "TE", 6.0, None),
            (3, "Scrub", "TE", 1.0, None),
            (4, "Decent", "TE", 9.0, None),
        )
        current = {1: "TE", 2: "IR", 3: "BE", 4: "BE"}
        league = self._League(ir_slots=1, bench_slots=0)

        alert = automode.ir_plan_for(roster, current, league, ir_return="alert")
        assert alert.blocked and alert.drop_candidate is None

        drop = automode.ir_plan_for(roster, current, league, ir_return="drop")
        # The cheapest bench player is named -- and that is all that happens.
        assert drop.drop_candidate == (3, "Scrub")
        _, stash = automode.lineup_and_ir_moves(roster, _shape(), current, drop)
        assert stash == [], "naming a drop must never move or drop anybody"
        assert "Scrub" in automode.ir_blocked_message(drop)

    def test_the_stash_is_a_second_transaction(self):
        # A hurt starter has to come OUT of his starting slot before he can go to
        # IR. Phase one benches him and promotes the healthy player; phase two
        # moves him to IR. Bundled together, one refused IR item would take the
        # whole lineup down with it.
        roster = self._roster(
            (1, "Hurt Stud", "TE", 0.0, "OUT"),
            (2, "Healthy TE", "TE", 8.0, None),
        )
        current = {1: "TE", 2: "BE"}
        plan = automode.ir_plan_for(
            roster, current, self._League(ir_slots=1, bench_slots=1)
        )
        moves, stash = automode.lineup_and_ir_moves(roster, _shape(), current, plan)

        by_id = {m.espn_player_id: m for m in moves}
        assert by_id[1].to_slot == "BE", "phase one parks him on the bench"
        assert by_id[2].to_slot == "TE", "and starts the healthy player"
        assert len(stash) == 1
        assert (stash[0].espn_player_id, stash[0].from_slot, stash[0].to_slot) == (1, "BE", "IR")

    def test_no_ir_slots_means_no_ir_plan(self):
        roster = self._roster((1, "Hurt Guy", "TE", 0.0, "OUT"))
        plan = automode.ir_plan_for(roster, {1: "BE"}, self._League(ir_slots=0))
        assert plan.to_ir == [] and plan.blocked == [] and plan.ir_slots == 0

    def test_the_return_preference_defaults_to_alert(self):
        class Config:
            auto_ir_return = None

        assert automode.resolve_ir_return(Config()) == "alert"
        assert automode.resolve_ir_return(None) == "alert"
        Config.auto_ir_return = "DROP"
        assert automode.resolve_ir_return(Config()) == "drop"
        Config.auto_ir_return = "nonsense"
        assert automode.resolve_ir_return(Config()) == "alert"


def test_the_status_plan_carries_the_ir_picture(drafted_league):
    # The IR panel on the Auto tab reads this, so it has to be in the payload --
    # and it is there whether or not the lineup tier is on, because a jammed IR
    # slot blocks every other move either way.
    _grant_auto_mode(drafted_league)
    assert drafted_league.post(
        "/api/season/automode/settings", json={"auto_mode": True, "auto_ir_return": "drop"}
    ).status_code == 200

    body = drafted_league.get("/api/season/automode").json()
    assert body["ir_return"] == "drop"
    assert "ir" in body["plan"]
    ir = body["plan"]["ir"]
    if ir is not None:
        assert set(ir) >= {"ir_slots", "ir_free", "to_ir", "from_ir", "blocked",
                           "drop_candidate", "needs_attention"}


def test_a_bad_ir_choice_is_refused(drafted_league):
    _grant_auto_mode(drafted_league)
    assert drafted_league.post(
        "/api/season/automode/settings", json={"auto_ir_return": "trade_him"}
    ).status_code == 422


def _shape():
    from app.engine.league_shape import LeagueShape

    return LeagueShape(team_count=10, dedicated={"TE": 1}, bench_slots=2)
