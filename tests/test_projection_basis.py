"""Say when a week's numbers are estimates rather than real projections.

Reported from real use: "week 1 and 2 give values but starting week three
nothing changes". That is correct behaviour -- ESPN publishes per-week splits
only a short way ahead, and the fallback spreads the season total evenly, so
every future week is the same number -- but presented without explanation it
reads as a broken screen.
"""

from __future__ import annotations


class TestProjectionBasis:
    def _lineup(self, client, week: int) -> dict:
        response = client.get(f"/api/season/lineup?week={week}")
        assert response.status_code == 200, response.text
        return response.json()

    def test_the_basis_is_always_reported(self, drafted_league):
        body = self._lineup(drafted_league, 1)
        assert body["projection_basis"] in {
            "espn_weekly", "season_average", "mixed", "none",
        }

    def test_counts_are_consistent_with_the_basis(self, drafted_league):
        body = self._lineup(drafted_league, 1)
        if body["projection_basis"] == "season_average":
            assert body["estimated_count"] == body["roster_count"]
        elif body["projection_basis"] == "espn_weekly":
            assert body["estimated_count"] == 0
        elif body["projection_basis"] == "mixed":
            assert 0 < body["estimated_count"] < body["roster_count"]

    def test_the_estimated_list_matches_the_count(self, drafted_league):
        body = self._lineup(drafted_league, 1)
        assert len(body["estimated_projections"]) == body["estimated_count"]

    def test_a_week_espn_has_not_published_is_reported_as_estimated(self, drafted_league):
        """The specific complaint: week 3 onwards never changes.

        The demo provider publishes a split for every week, which real ESPN does
        not, so the fallback is provoked here by removing week 15's splits.
        """
        from app.db import session_scope
        from app.models import PlayerWeeklyProjection

        with session_scope() as session:
            session.query(PlayerWeeklyProjection).filter(
                PlayerWeeklyProjection.week == 15
            ).delete(synchronize_session=False)
            session.commit()

        body = self._lineup(drafted_league, 15)
        assert body["projection_basis"] == "season_average"
        assert body["estimated_count"] == body["roster_count"]
        assert body["estimated_count"] > 0

    def test_two_unpublished_weeks_give_the_same_total_and_say_why(self, drafted_league):
        """Identical consecutive weeks are the symptom that looked like a bug."""
        from app.db import session_scope
        from app.models import PlayerWeeklyProjection

        with session_scope() as session:
            session.query(PlayerWeeklyProjection).filter(
                PlayerWeeklyProjection.week.in_([14, 15])
            ).delete(synchronize_session=False)
            session.commit()

        first = self._lineup(drafted_league, 14)
        second = self._lineup(drafted_league, 15)
        assert first["projected_points"] == second["projected_points"]
        assert first["projection_basis"] == "season_average"
        assert second["projection_basis"] == "season_average"

    def test_identical_far_weeks_are_flagged_not_silently_repeated(self, drafted_league):
        """If two weeks give the same total, the reader must be told why."""
        first = self._lineup(drafted_league, 14)
        second = self._lineup(drafted_league, 15)
        if first["projected_points"] == second["projected_points"]:
            assert first["projection_basis"] != "espn_weekly"
            assert second["projection_basis"] != "espn_weekly"
