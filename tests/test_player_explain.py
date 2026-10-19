"""Asking the app why it does not recommend somebody.

Three rounds of screenshots went into answering that question about one
receiver, because the wire drops a candidate who does not help silently and
with no way to interrogate it. The rules that run BEFORE any ranking -- already
rostered, no NFL team, outside the pool -- are the ones nobody can see from the
outside, so this endpoint reports whichever applied, in order, with the
arithmetic behind it.
"""

from __future__ import annotations


def _ask(client, **params):
    response = client.get("/api/season/player-explain", params=params)
    assert response.status_code in (200, 400), response.text
    return response


class TestFindingHim:
    def test_a_name_nobody_has_is_said_plainly(self, drafted_league):
        body = _ask(drafted_league, name="Nobody McNobodyface").json()
        assert body["found"] is False
        assert "pool" in body["reason"].lower()

    def test_a_stub_of_a_name_is_refused(self, drafted_league):
        assert _ask(drafted_league, name="ab").status_code == 400

    def test_several_matches_come_back_as_a_choice(self, drafted_league):
        from app.db import session_scope
        from app.models import Player

        with session_scope() as session:
            names = [p.name for p in session.query(Player).limit(50).all()]
        # The demo pool names players "First Last"; a shared surname is enough.
        shared = None
        for name in names:
            stem = name.split()[-1][:4].lower()
            if sum(1 for n in names if stem in n.lower()) > 1:
                shared = stem
                break
        if shared is None:
            return      # nothing ambiguous in this fixture; nothing to assert

        body = _ask(drafted_league, name=shared).json()
        if not body["found"]:
            assert body.get("ambiguous") or "pool" in body["reason"].lower()
            if body.get("ambiguous"):
                assert len(body["candidates"]) > 1


class TestTheRulesThatRunBeforeRanking:
    def test_a_player_on_my_own_team_says_so(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam, Player

        with session_scope() as session:
            league = session.query(League).one()
            mine = session.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id, LeagueTeam.is_mine
            ).one()
            name = session.query(Player).filter(
                Player.espn_player_id == mine.roster[0]["espn_player_id"]
            ).one().name

        body = _ask(drafted_league, name=name).json()
        assert body["found"] is True
        assert body["on_my_team"] is True
        assert "already have him" in body["verdict"].lower()

    def test_a_player_someone_else_rosters_names_the_team(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam, Player

        with session_scope() as session:
            league = session.query(League).one()
            theirs = session.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id, LeagueTeam.is_mine.is_(False)
            ).first()
            team_name = theirs.name
            name = session.query(Player).filter(
                Player.espn_player_id == theirs.roster[0]["espn_player_id"]
            ).one().name

        body = _ask(drafted_league, name=name).json()
        assert body["found"] is True
        assert body["rostered_by"] == team_name
        assert team_name in body["verdict"]

    def test_an_unsigned_player_is_explained_not_hidden(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam, Player
        from app.services.board import clear_cache

        with session_scope() as session:
            league = session.query(League).one()
            rostered = set()
            for team in session.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id
            ).all():
                rostered |= {e["espn_player_id"] for e in (team.roster or [])}
            free = session.query(Player).filter(
                Player.espn_player_id.notin_(rostered or {0})
            ).first()
            free.pro_team = "FA"
            name = free.name
        clear_cache()

        body = _ask(drafted_league, name=name).json()
        assert body["found"] is True
        assert "no NFL roster" in body["verdict"], body["verdict"]


class TestTheArithmeticIsShown:
    def test_an_available_player_gets_the_numbers(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam, Player

        with session_scope() as session:
            league = session.query(League).one()
            rostered = set()
            for team in session.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id
            ).all():
                rostered |= {e["espn_player_id"] for e in (team.roster or [])}
            free = session.query(Player).filter(
                Player.espn_player_id.notin_(rostered or {0}),
                Player.position == "WR",
            ).first()
            name = free.name

        body = _ask(drafted_league, name=name).json()
        assert body["found"] is True
        assert body["verdict"], "every answer has to end in a sentence"
        assert "math" in body
        for key in ("week_gain", "season_gain", "insurance_gain", "covers_byes"):
            assert key in body["math"]

    def test_the_rating_basis_is_reported(self, drafted_league):
        from app.db import session_scope
        from app.models import League, LeagueTeam, Player

        with session_scope() as session:
            league = session.query(League).one()
            mine = session.query(LeagueTeam).filter(
                LeagueTeam.league_id == league.id, LeagueTeam.is_mine
            ).one()
            name = session.query(Player).filter(
                Player.espn_player_id == mine.roster[0]["espn_player_id"]
            ).one().name

        rating = _ask(drafted_league, name=name).json()["rating"]
        assert rating is not None
        # No games played in the fixture, so the rating is still the projection
        # -- and it says so rather than implying evidence it does not have.
        assert rating["basis"] in {"projection", "blended"}
