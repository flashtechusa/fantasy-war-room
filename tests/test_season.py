"""Phase 8 -- start/sit, waivers and trades."""

from __future__ import annotations

import pytest

from app.engine.league_shape import LeagueShape
from app.engine.trades import analyse_trade
from app.engine.waivers import recommend_waivers
from app.engine.weekly import CLOSE_CALL_POINTS, WeeklyPlayer, optimise_lineup


def wp(name, position, week, season=None, **kwargs) -> WeeklyPlayer:
    return WeeklyPlayer(
        espn_player_id=abs(hash(name)) % 1_000_000,
        name=name,
        position=position,
        week_points=week,
        season_points=season if season is not None else week * 17,
        **kwargs,
    )


@pytest.fixture
def shape() -> LeagueShape:
    return LeagueShape.from_slots(
        12, {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}, bench_slots=6
    )


@pytest.fixture
def roster() -> list[WeeklyPlayer]:
    return [
        wp("QB1", "QB", 22), wp("QB2", "QB", 15),
        wp("RB1", "RB", 20), wp("RB2", "RB", 14), wp("RB3", "RB", 9),
        wp("WR1", "WR", 19), wp("WR2", "WR", 16), wp("WR3", "WR", 13),
        wp("TE1", "TE", 11), wp("K1", "K", 8), wp("DST1", "DST", 7),
    ]


class TestStartSit:
    def test_best_players_start(self, roster, shape):
        result = optimise_lineup(roster, shape, week=5)
        started = {d.player.name for d in result.starters if d.player}
        assert {"QB1", "RB1", "RB2", "WR1", "WR2", "TE1"} <= started
        assert "QB2" not in started

    def test_flex_takes_the_best_leftover(self, roster, shape):
        result = optimise_lineup(roster, shape, week=5)
        flex = next(d for d in result.starters if d.slot == "FLEX")
        assert flex.player.name == "WR3"      # 13 beats RB3's 9

    def test_projected_points_is_the_sum_of_starters(self, roster, shape):
        result = optimise_lineup(roster, shape, week=5)
        total = sum(d.player.week_points for d in result.starters if d.player)
        assert result.projected_points == pytest.approx(total, abs=0.01)

    def test_bench_holds_everyone_else(self, roster, shape):
        result = optimise_lineup(roster, shape, week=5)
        started = {d.player.espn_player_id for d in result.starters if d.player}
        assert all(p.espn_player_id not in started for p in result.bench)
        assert len(started) + len(result.bench) == len(roster)

    def test_bye_week_player_is_flagged_and_benched(self, shape):
        roster = [
            wp("RB1", "RB", 0.0, season=280, bye_week=5),
            wp("RB2", "RB", 15), wp("RB3", "RB", 12),
        ]
        result = optimise_lineup(roster, shape, week=5)
        started = {d.player.name for d in result.starters if d.player}
        assert "RB2" in started and "RB3" in started
        assert any("bye" in w for w in result.warnings)

    def test_a_forced_start_still_warns(self, shape):
        """Only one DST and he's on bye: start him, but say so."""
        roster = [wp("DST1", "DST", 0.0, season=100, bye_week=5)]
        result = optimise_lineup(roster, shape, week=5)
        dst = next(d for d in result.starters if d.slot == "DST")
        assert dst.player.name == "DST1"
        assert "bye" in dst.warning

    def test_close_calls_are_surfaced(self, shape):
        # Four WRs for three eligible slots, so there is a real decision.
        roster = [
            wp("WR1", "WR", 12.0), wp("WR2", "WR", 11.5),
            wp("WR3", "WR", 11.4), wp("WR4", "WR", 11.3),
        ]
        result = optimise_lineup(roster, shape, week=5)
        assert result.close_calls
        assert all(abs(c.margin) < CLOSE_CALL_POINTS for c in result.close_calls)

    def test_a_clear_gap_is_not_a_close_call(self, shape):
        roster = [
            wp("WR1", "WR", 22.0), wp("WR2", "WR", 21.0),
            wp("WR3", "WR", 20.0), wp("WR4", "WR", 4.0),
        ]
        result = optimise_lineup(roster, shape, week=5)
        assert not result.close_calls

    def test_every_started_slot_explains_itself(self, roster, shape):
        result = optimise_lineup(roster, shape, week=5)
        assert all(d.reason for d in result.starters if d.player)

    def test_weekly_beats_naive_season_ordering(self, shape):
        """The whole point: this week's numbers, not season reputation."""
        # Four bodies for three RB-eligible slots: the stud's bye must cost him
        # his spot even though he leads the roster on season points.
        roster = [
            wp("Stud on bye", "RB", 0.0, season=300, bye_week=5),
            wp("Backup", "RB", 14.0, season=90),
            wp("Other", "RB", 12.0, season=85),
            wp("Fourth", "RB", 10.0, season=80),
        ]
        result = optimise_lineup(roster, shape, week=5)
        started = {d.player.name for d in result.starters if d.player}
        assert "Stud on bye" not in started
        assert result.points_vs_naive > 0

    def test_unfilled_slots_are_reported(self, shape):
        result = optimise_lineup([wp("RB1", "RB", 15)], shape, week=5)
        assert "QB" in result.unfilled_slots

    def test_empty_roster_is_safe(self, shape):
        result = optimise_lineup([], shape, week=3)
        assert result.projected_points == 0.0
        assert result.unfilled_slots


class TestWaivers:
    def _free_agents(self):
        return [
            wp("Great FA", "RB", 18.0, season=240),
            wp("Streamer DST", "DST", 12.0, season=95),
            wp("Useless", "WR", 1.0, season=12),
        ]

    def test_upgrades_are_ranked_first(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5)
        assert targets
        assert targets[0].player.name == "Great FA"
        assert targets[0].priority == pytest.approx(100.0, abs=0.1)

    def test_players_who_do_not_help_are_omitted(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5)
        assert all(t.player.name != "Useless" for t in targets)

    def test_every_target_names_a_drop_when_the_roster_is_full(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5,
                                    roster_is_full=True)
        assert all(t.drop is not None for t in targets)

    def test_no_drop_needed_when_there_is_a_spot(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5,
                                    roster_is_full=False)
        assert all(t.drop is None for t in targets)
        assert any("open roster spot" in r for r in targets[0].reasons)

    def test_starters_are_never_drop_candidates(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5)
        dropped = {t.drop.name for t in targets if t.drop}
        assert "QB1" not in dropped and "RB1" not in dropped and "WR1" not in dropped

    def test_high_vor_bench_players_are_protected(self, shape):
        keeper = wp("Injured stud", "RB", 0.0, season=250)
        keeper.vor = 120.0
        roster = [wp("RB1", "RB", 18), wp("RB2", "RB", 15), wp("QB1", "QB", 20), keeper]
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5)
        assert all(t.drop is None or t.drop.name != "Injured stud" for t in targets)

    def test_faab_scales_with_the_budget(self, roster, shape):
        small = recommend_waivers(roster, self._free_agents(), shape, 5, faab_budget=100)
        big = recommend_waivers(roster, self._free_agents(), shape, 5, faab_budget=1000)
        assert big[0].faab_bid > small[0].faab_bid

    def test_no_faab_bid_without_a_budget(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, 5, faab_budget=0)
        assert all(t.faab_bid == 0 for t in targets)

    def test_verdicts_distinguish_a_stud_from_a_streamer(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5)
        by_name = {t.player.name: t for t in targets}
        assert by_name["Great FA"].verdict in {"must-add", "starter"}

    def test_every_target_explains_itself(self, roster, shape):
        targets = recommend_waivers(roster, self._free_agents(), shape, week=5)
        assert all(len(t.reasons) >= 2 for t in targets)

    def test_empty_wire_is_handled(self, roster, shape):
        assert recommend_waivers(roster, [], shape, week=5) == []


class TestTrades:
    def test_a_clear_upgrade_is_recommended(self, roster, shape):
        give = [next(p for p in roster if p.name == "RB3")]
        receive = [wp("Elite WR", "WR", 24.0, season=300)]
        result = analyse_trade(roster, give, receive, shape, week=5)
        assert result.my_side.season_delta > 0
        assert result.verdict in {"accept", "lean accept"}

    def test_a_clear_downgrade_is_rejected(self, roster, shape):
        give = [next(p for p in roster if p.name == "RB1")]
        receive = [wp("Scrub", "RB", 2.0, season=20)]
        result = analyse_trade(roster, give, receive, shape, week=5)
        assert result.my_side.season_delta < 0
        assert result.verdict in {"reject", "lean reject"}

    def test_trading_bench_depth_barely_moves_the_lineup(self, roster, shape):
        give = [next(p for p in roster if p.name == "RB3")]
        receive = [wp("Similar bench", "RB", 9.0, season=150)]
        result = analyse_trade(roster, give, receive, shape, week=5)
        assert result.verdict == "neutral"

    def test_lineup_delta_not_raw_points_decides(self, roster, shape):
        """Giving up more total points can still be a win."""
        give = [
            next(p for p in roster if p.name == "RB3"),
            next(p for p in roster if p.name == "QB2"),
        ]
        receive = [wp("Upgrade", "WR", 25.0, season=320)]
        result = analyse_trade(roster, give, receive, shape, week=5)
        given_total = sum(p.season_points for p in give)
        assert receive[0].season_points < given_total * 2   # not a blowout on raw points
        assert result.my_side.season_delta > 0              # but the lineup improves

    def test_both_sides_are_evaluated(self, roster, shape):
        their_roster = [
            wp("TheirRB", "RB", 21, season=290), wp("TheirWR", "WR", 17, season=230),
            wp("TheirQB", "QB", 20, season=280),
        ]
        result = analyse_trade(
            roster,
            give=[next(p for p in roster if p.name == "WR1")],
            receive=[their_roster[0]],
            shape=shape, week=5, their_roster=their_roster, their_label="Rivals",
        )
        assert result.their_side is not None
        assert result.their_side.label == "Rivals"
        assert any("Rivals" in r for r in result.reasons)

    def test_roster_count_change_is_reported(self, roster, shape):
        result = analyse_trade(
            roster,
            give=[next(p for p in roster if p.name == "RB3"),
                  next(p for p in roster if p.name == "QB2")],
            receive=[wp("One guy", "WR", 20, season=260)],
            shape=shape, week=5,
        )
        assert result.my_side.roster_change == -1
        assert any("short" in r for r in result.reasons)

    def test_thinning_a_position_is_flagged(self, shape):
        roster = [wp("QB1", "QB", 22), wp("RB1", "RB", 20), wp("RB2", "RB", 18),
                  wp("WR1", "WR", 19), wp("WR2", "WR", 17), wp("TE1", "TE", 11)]
        result = analyse_trade(
            roster,
            give=[roster[1]],
            receive=[wp("New WR", "WR", 21, season=290)],
            shape=shape, week=5,
        )
        assert any("injury" in note or "unfilled" in note.lower()
                   for note in result.my_side.notes)

    def test_position_changes_are_counted(self, roster, shape):
        result = analyse_trade(
            roster,
            give=[next(p for p in roster if p.name == "RB3")],
            receive=[wp("New WR", "WR", 15, season=200)],
            shape=shape, week=5,
        )
        assert result.my_side.position_changes["RB"] == -1
        assert result.my_side.position_changes["WR"] == 1

    def test_every_trade_explains_itself(self, roster, shape):
        result = analyse_trade(
            roster,
            give=[next(p for p in roster if p.name == "RB3")],
            receive=[wp("New", "WR", 15, season=200)],
            shape=shape, week=5,
        )
        assert result.summary and len(result.reasons) >= 2


class TestSeasonApi:
    @pytest.fixture
    def ready(self, client):
        assert client.post("/api/league/import", json={}).status_code == 201
        # Give my team a roster, the way ESPN would after a draft.
        from sqlalchemy import select
        from app.db import session_scope
        from app.models import Player
        from app.services.importer import get_active_league

        with session_scope() as session:
            league = get_active_league(session)
            players = session.scalars(
                select(Player).where(Player.season == league.season)
            ).all()
            by_position: dict[str, list] = {}
            for player in players:
                by_position.setdefault(player.position, []).append(player)
            for group in by_position.values():
                group.sort(key=lambda p: p.adp or 999)
            picked = (
                by_position["QB"][:2] + by_position["RB"][:4] + by_position["WR"][:5]
                + by_position["TE"][:2] + by_position["K"][:1] + by_position["DST"][:1]
            )
            team = next(t for t in league.teams if t.espn_team_id == 1)
            team.is_mine = True
            team.roster = [
                {
                    "espn_player_id": p.espn_player_id, "name": p.name,
                    "position": p.position, "slot": "BE", "pro_team": p.pro_team,
                }
                for p in picked
            ]
            session.commit()
        return client

    def test_lineup_endpoint(self, ready):
        body = ready.get("/api/season/lineup", params={"week": 5}).json()
        assert body["week"] == 5
        assert body["projected_points"] > 0
        assert body["starters"]
        assert all("reason" in s for s in body["starters"])

    def test_lineup_without_a_roster_explains_what_to_do(self, client):
        client.post("/api/league/import", json={})
        response = client.get("/api/season/lineup", params={"week": 5})
        assert response.status_code == 409
        assert "team" in response.json()["detail"].lower()

    def test_never_starts_a_player_ruled_out(self, ready):
        body = ready.get("/api/season/lineup", params={"week": 5}).json()
        for slot in body["starters"]:
            player = slot["player"]
            if player and player["injury_status"] in {"OUT", "INJURY_RESERVE"}:
                # Allowed only when nothing else can fill the slot.
                assert slot["next_best"] is None

    def test_waivers_endpoint(self, ready):
        body = ready.get("/api/season/waivers", params={"week": 5, "limit": 5}).json()
        assert body["free_agents_considered"] > 0
        assert body["targets"]
        for target in body["targets"]:
            assert target["reasons"]
            assert target["priority"] >= 0

    def test_trade_endpoint(self, ready):
        roster = ready.get("/api/season/roster", params={"week": 5}).json()["players"]
        waiver = ready.get("/api/season/waivers", params={"week": 5, "limit": 1}).json()
        target = waiver["targets"][0]["player"]["espn_player_id"]

        body = ready.post("/api/season/trade", json={
            "give": [roster[-1]["espn_player_id"]],
            "receive": [target],
            "week": 5,
        }).json()
        assert body["verdict"] in {
            "accept", "lean accept", "neutral", "lean reject", "reject"
        }
        assert body["reasons"]
        assert body["my_side"]["label"] == "Your team"

    def test_trade_rejects_an_empty_proposal(self, ready):
        assert ready.post("/api/season/trade", json={"give": [], "receive": []}).status_code == 400

    def test_trade_reports_unknown_players(self, ready):
        response = ready.post("/api/season/trade", json={"give": [], "receive": [999999999]})
        assert response.status_code == 404

    def test_roster_endpoint_lists_teams(self, ready):
        body = ready.get("/api/season/roster", params={"week": 5}).json()
        assert body["players"]
        assert any(t["is_mine"] for t in body["teams"])
