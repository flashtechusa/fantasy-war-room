"""What is worth interrupting someone for, and what is not."""

from __future__ import annotations

from app.engine.alerts import (
    START_SIT_ALERT_POINTS,
    collect,
    detect_injury_changes,
    detect_lineup_problems,
    detect_start_sit,
    detect_waiver_targets,
)
from app.engine.waivers import WaiverTarget
from app.engine.weekly import SlotDecision, StartSitResult, WeeklyPlayer


def player(
    pid: int,
    name: str,
    position: str = "RB",
    *,
    week_points: float = 12.0,
    injury_status: str = "ACTIVE",
    bye_week: int | None = None,
) -> WeeklyPlayer:
    return WeeklyPlayer(
        espn_player_id=pid,
        name=name,
        position=position,
        week_points=week_points,
        season_points=week_points * 17,
        injury_status=injury_status,
        bye_week=bye_week,
    )


def result(
    *decisions: SlotDecision,
    week: int = 5,
    unfilled: list[str] | None = None,
) -> StartSitResult:
    return StartSitResult(
        week=week,
        starters=list(decisions),
        unfilled_slots=unfilled or [],
    )


class TestLineupProblems:
    """The alert that justifies the feature."""

    def test_a_starter_who_is_out_is_critical(self):
        alerts = detect_lineup_problems(
            result(SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="OUT")))
        )
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].kind == "lineup_broken"
        assert "Bijan" in alerts[0].body

    def test_a_starter_on_bye_is_critical(self):
        # week_points of 0 with a bye week set is what `on_bye` keys off.
        benched = player(2, "Kupp", position="WR", week_points=0.0, bye_week=5)
        alerts = detect_lineup_problems(result(SlotDecision(slot="WR1", player=benched)))
        assert len(alerts) == 1
        assert "on bye" in alerts[0].body

    def test_a_healthy_lineup_says_nothing(self):
        alerts = detect_lineup_problems(
            result(
                SlotDecision(slot="RB1", player=player(1, "Bijan")),
                SlotDecision(slot="WR1", player=player(2, "Kupp", position="WR")),
            )
        )
        assert alerts == []

    def test_questionable_is_not_treated_as_broken(self):
        """Questionable players still play. Alerting on them is crying wolf."""
        alerts = detect_lineup_problems(
            result(SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="QUESTIONABLE")))
        )
        assert alerts == []

    def test_an_empty_slot_is_reported_separately(self):
        alerts = detect_lineup_problems(result(unfilled=["FLEX"]))
        assert len(alerts) == 1
        assert alerts[0].kind == "lineup_empty"
        assert "FLEX" in alerts[0].body

    def test_several_broken_starters_are_one_alert(self):
        """One notification per sitting, not one per player."""
        alerts = detect_lineup_problems(
            result(
                SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="OUT")),
                SlotDecision(slot="RB2", player=player(2, "Walker", injury_status="IR")),
            )
        )
        assert len(alerts) == 1
        assert "2 starters" in alerts[0].title

    def test_the_key_tracks_which_players_are_broken(self):
        """A different problem must be able to alert again; the same one must not."""
        first = detect_lineup_problems(
            result(SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="OUT")))
        )[0]
        same = detect_lineup_problems(
            result(SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="OUT")))
        )[0]
        different = detect_lineup_problems(
            result(SlotDecision(slot="RB1", player=player(9, "Someone Else", injury_status="OUT")))
        )[0]
        assert first.key == same.key
        assert first.key != different.key


class TestStartSit:
    def test_a_clear_upgrade_alerts(self):
        alerts = detect_start_sit(
            result(
                SlotDecision(
                    slot="FLEX",
                    player=player(1, "Starter", week_points=8.0),
                    next_best=player(2, "Bench Guy", week_points=14.0),
                    margin=6.0,
                )
            )
        )
        assert len(alerts) == 1
        assert alerts[0].severity == "important"
        assert "Bench Guy" in alerts[0].body

    def test_a_margin_inside_the_noise_floor_stays_quiet(self):
        """Weekly projections are not precise enough for a 2-point edge."""
        alerts = detect_start_sit(
            result(
                SlotDecision(
                    slot="FLEX",
                    player=player(1, "Starter", week_points=10.0),
                    next_best=player(2, "Bench Guy", week_points=12.0),
                    margin=2.0,
                )
            )
        )
        assert alerts == []

    def test_the_threshold_is_inclusive(self):
        alerts = detect_start_sit(
            result(
                SlotDecision(
                    slot="FLEX",
                    player=player(1, "Starter"),
                    next_best=player(2, "Bench Guy"),
                    margin=START_SIT_ALERT_POINTS,
                )
            )
        )
        assert len(alerts) == 1

    def test_the_body_leads_with_the_biggest_gain(self):
        alerts = detect_start_sit(
            result(
                SlotDecision(
                    slot="WR2",
                    player=player(1, "Small Gain"),
                    next_best=player(2, "Minor"),
                    margin=3.5,
                ),
                SlotDecision(
                    slot="FLEX",
                    player=player(3, "Big Gain"),
                    next_best=player(4, "Major"),
                    margin=9.0,
                ),
            )
        )
        assert "Major" in alerts[0].body


def target(
    pid: int, name: str, *, priority: float, verdict: str = "depth", faab: int = 0
) -> WaiverTarget:
    return WaiverTarget(
        player=player(pid, name),
        week_gain=2.0,
        season_gain=20.0,
        drop=None,
        priority=priority,
        faab_bid=faab,
        faab_pct=0.0,
        verdict=verdict,
    )


class TestWaivers:
    def test_a_must_add_alerts_regardless_of_priority(self):
        alerts = detect_waiver_targets([target(1, "Breakout", priority=10.0, verdict="must-add")], 5)
        assert len(alerts) == 1
        assert alerts[0].severity == "important"

    def test_low_priority_depth_stays_quiet(self):
        alerts = detect_waiver_targets([target(1, "Some Guy", priority=20.0)], 5)
        assert alerts == []

    def test_targets_are_batched_into_one_notification(self):
        alerts = detect_waiver_targets(
            [
                target(1, "First", priority=90.0, verdict="starter"),
                target(2, "Second", priority=80.0, verdict="starter"),
                target(3, "Third", priority=70.0, verdict="starter"),
            ],
            5,
        )
        assert len(alerts) == 1
        assert "+2 more" in alerts[0].body

    def test_the_bid_is_in_the_notification_body(self):
        """The point of the alert is acting on it without opening the app first."""
        alerts = detect_waiver_targets(
            [target(1, "Breakout", priority=90.0, verdict="must-add", faab=23)], 5
        )
        assert "$23" in alerts[0].body

    def test_it_points_at_the_waivers_screen(self):
        alerts = detect_waiver_targets([target(1, "X", priority=90.0, verdict="must-add")], 5)
        assert alerts[0].screen == "waivers"


class TestInjuryChanges:
    def test_getting_worse_alerts(self):
        alerts = detect_injury_changes(
            [player(1, "Bijan", injury_status="OUT")],
            {1: "QUESTIONABLE"},
        )
        assert len(alerts) == 1
        assert "Bijan" in alerts[0].body

    def test_getting_better_does_not(self):
        """Good news is not urgent, and the Week screen already shows it."""
        alerts = detect_injury_changes(
            [player(1, "Bijan", injury_status="ACTIVE")],
            {1: "DOUBTFUL"},
        )
        assert alerts == []

    def test_no_change_says_nothing(self):
        alerts = detect_injury_changes(
            [player(1, "Bijan", injury_status="QUESTIONABLE")],
            {1: "QUESTIONABLE"},
        )
        assert alerts == []

    def test_a_player_we_have_never_seen_is_not_an_alert(self):
        """First import would otherwise alert on every injured player at once."""
        alerts = detect_injury_changes([player(1, "Bijan", injury_status="OUT")], {})
        assert alerts == []


class TestCollect:
    def _broken(self) -> StartSitResult:
        return result(
            SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="OUT")),
            SlotDecision(
                slot="FLEX",
                player=player(3, "Starter"),
                next_best=player(4, "Bench Guy"),
                margin=8.0,
            ),
        )

    def test_critical_comes_first(self):
        alerts = collect(
            start_sit=self._broken(),
            waiver_targets=[target(9, "Pickup", priority=90.0, verdict="must-add")],
        )
        assert [a.severity for a in alerts][0] == "critical"
        assert all(
            alerts[i].rank <= alerts[i + 1].rank for i in range(len(alerts) - 1)
        )

    def test_already_sent_alerts_are_suppressed(self):
        first = collect(start_sit=self._broken())
        assert first
        again = collect(start_sit=self._broken(), already_sent={a.key for a in first})
        assert again == []

    def test_a_new_problem_still_gets_through(self):
        first = collect(start_sit=self._broken())
        worse = result(
            SlotDecision(slot="RB1", player=player(1, "Bijan", injury_status="OUT")),
            SlotDecision(slot="RB2", player=player(2, "Walker", injury_status="OUT")),
        )
        again = collect(start_sit=worse, already_sent={a.key for a in first})
        assert any(a.kind == "lineup_broken" for a in again)

    def test_nothing_wrong_means_no_notifications(self):
        quiet = result(SlotDecision(slot="RB1", player=player(1, "Bijan")))
        assert collect(start_sit=quiet, waiver_targets=[]) == []

    def test_every_alert_carries_something_to_show(self):
        for alert in collect(
            start_sit=self._broken(),
            waiver_targets=[target(9, "Pickup", priority=90.0, verdict="must-add", faab=5)],
        ):
            assert alert.title and alert.body and alert.key
            assert alert.severity in {"critical", "important", "fyi"}
