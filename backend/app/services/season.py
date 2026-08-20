"""Builds the in-season views from stored data.

Post-draft, "my roster" comes from ESPN's team roster rather than the draft
session -- it stays correct through adds, drops and trades, which the draft log
never sees.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.scoring import LeagueScoring
from ..engine.valuation import ValuationEngine
from ..engine.weekly import WeeklyPlayer
from ..models import (
    HistoricalDraftPick,
    League,
    Player,
    PlayerWeeklyProjection,
    ProjectionSource,
)

#: Positions the season tools reason about.
KNOWN_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}


def _enabled_sources(session: Session) -> dict[str, float]:
    weights = {
        source.key: source.weight
        for source in session.scalars(
            select(ProjectionSource).where(ProjectionSource.enabled.is_(True))
        ).all()
    }
    return weights or {"espn": 1.0, "demo": 1.0}


def weekly_points_by_player(
    session: Session, league: League, scoring: LeagueScoring, week: int
) -> dict[int, float]:
    """{espn_player_id: points for this week} under the league's own rules."""
    weights = _enabled_sources(session)

    rows = session.execute(
        select(PlayerWeeklyProjection, Player)
        .join(Player, Player.id == PlayerWeeklyProjection.player_id)
        .where(
            PlayerWeeklyProjection.week == week,
            Player.season == league.season,
            Player.source == league.source,
        )
    ).all()

    blended: dict[int, tuple[float, float]] = {}   # id -> (weighted points, weight)
    for projection, player in rows:
        weight = weights.get(projection.source_key)
        if weight is None:
            continue
        points = scoring.score(projection.raw_stats or {}, player.position)
        if points == 0.0 and projection.source_points:
            points = float(projection.source_points)
        total, seen = blended.get(player.espn_player_id, (0.0, 0.0))
        blended[player.espn_player_id] = (total + points * weight, seen + weight)

    return {
        pid: round(total / weight, 2)
        for pid, (total, weight) in blended.items()
        if weight > 0
    }


def build_weekly_players(
    session: Session,
    league: League,
    engine: ValuationEngine,
    week: int,
    espn_player_ids: set[int] | None = None,
    availability: set[str] | None = None,
    limit: int | None = None,
) -> list[WeeklyPlayer]:
    """WeeklyPlayer records for a set of players, scored for one week.

    Falls back to the season projection spread over the remaining games when a
    week has no published split, and flags that it did so -- an estimate that
    silently passes for a real projection is worse than no number at all.
    """
    scoring = engine.scoring
    week_points = weekly_points_by_player(session, league, scoring, week)

    stmt = select(Player).where(
        Player.season == league.season, Player.source == league.source
    )
    if espn_player_ids is not None:
        if not espn_player_ids:
            return []
        stmt = stmt.where(Player.espn_player_id.in_(espn_player_ids))
    if availability:
        stmt = stmt.where(Player.availability.in_(availability))

    players = session.scalars(stmt).all()
    remaining_weeks = max(league.regular_season_weeks - week + 1, 1)

    out: list[WeeklyPlayer] = []
    for player in players:
        if player.position not in KNOWN_POSITIONS:
            continue
        season_points = engine.points_for(player.espn_player_id)

        if player.espn_player_id in week_points:
            points = week_points[player.espn_player_id]
            real = True
        else:
            # No weekly split: spread what's left of the season evenly.
            points = round(season_points / max(league.regular_season_weeks, 1), 2)
            real = False

        # A player who isn't playing scores nothing, whatever the projection
        # says. Starting someone ruled out is the single most expensive lineup
        # mistake available, so it is corrected here rather than left to a
        # warning the optimiser might out-vote.
        status = (player.injury_status or "").upper()
        if player.bye_week == week:
            points = 0.0
        elif status in {"OUT", "INJURY_RESERVE", "SUSPENSION"}:
            points = 0.0
        elif status == "DOUBTFUL":
            points = round(points * 0.35, 2)

        out.append(
            WeeklyPlayer(
                espn_player_id=player.espn_player_id,
                name=player.name,
                position=player.position,
                pro_team=player.pro_team,
                week_points=points,
                # Rest-of-season value, not the full-season total: a trade in
                # week 10 shouldn't be judged on points already scored.
                season_points=round(
                    season_points * remaining_weeks / max(league.regular_season_weeks, 1), 2
                ),
                vor=engine.replacement.vor(player.position, season_points),
                bye_week=player.bye_week,
                injury_status=player.injury_status,
                percent_owned=player.percent_owned,
                availability=player.availability,
                week_projection_is_real=real,
            )
        )

    out.sort(key=lambda p: p.season_points, reverse=True)
    return out[:limit] if limit else out


def my_team(session: Session, league: League):
    """The LeagueTeam flagged as mine, if any."""
    return next((team for team in league.teams if team.is_mine), None)


def propose_trades(
    session: Session,
    league: League,
    engine: ValuationEngine,
    week: int,
    *,
    horizon: str = "season",
    include_longshots: bool = True,
) -> dict:
    """Find trades with every other team that improve my starting lineup.

    Assembles my roster and every opponent's roster as scored `WeeklyPlayer`s in
    one pass, then hands them to the engine's `find_trades`. Returns the engine's
    result dict (mutual / longshots) or a `reason` when there is nothing to work
    with (no roster imported yet).
    """
    from ..engine.trades import find_trades
    from ..services.board import league_shape

    mine_ids = my_roster_ids(session, league)
    if not mine_ids:
        return {"horizon": horizon, "mutual": [], "longshots": [], "reason": "no_my_roster"}

    rosters = rosters_by_team(session, league)
    mine_team = my_team(session, league)
    my_team_id = mine_team.espn_team_id if mine_team else None

    # Every player on any roster, scored once.
    everyone_ids: set[int] = set(mine_ids)
    for ids in rosters.values():
        everyone_ids |= ids
    players = {
        p.espn_player_id: p
        for p in build_weekly_players(session, league, engine, week, espn_player_ids=everyone_ids)
    }

    my_roster = [players[pid] for pid in mine_ids if pid in players]
    if not my_roster:
        return {"horizon": horizon, "mutual": [], "longshots": [], "reason": "no_my_roster"}

    labels = {t.espn_team_id: t.name for t in league.teams}
    teams: list[tuple[int, str, list[WeeklyPlayer]]] = []
    for team_id, ids in rosters.items():
        # Skip my own team (by id, and defensively by roster overlap).
        if team_id == my_team_id or ids == mine_ids:
            continue
        roster = [players[pid] for pid in ids if pid in players]
        if roster:
            teams.append((team_id, labels.get(team_id, f"Team {team_id}"), roster))

    if not teams:
        return {"horizon": horizon, "mutual": [], "longshots": [], "reason": "no_opponent_rosters"}

    return find_trades(
        my_roster, teams, league_shape(league), week,
        horizon=horizon, include_longshots=include_longshots,
    )


def espn_roster_ids(session: Session, league: League) -> set[int]:
    """Player ids on my ESPN roster. Empty when no team is identified.

    Deliberately has no fallback, so callers can tell "ESPN told us this" from
    "we inferred it from the draft log".
    """
    team = my_team(session, league)
    if not team or not team.roster:
        return set()
    return {
        int(entry["espn_player_id"])
        for entry in team.roster
        if entry.get("espn_player_id")
    }


def my_roster_ids(session: Session, league: League) -> set[int]:
    """Player ids on my ESPN roster, falling back to my draft picks."""
    ids = espn_roster_ids(session, league)
    if ids:
        return ids

    from ..models import DraftPick, DraftSession

    picks = session.scalars(
        select(DraftPick)
        .join(DraftSession, DraftSession.id == DraftPick.session_id)
        .where(DraftSession.league_id == league.id, DraftPick.is_mine.is_(True))
    ).all()
    return {pick.espn_player_id for pick in picks}


def team_roster_ids(league: League, espn_team_id: int) -> set[int]:
    team = next((t for t in league.teams if t.espn_team_id == espn_team_id), None)
    if not team or not team.roster:
        return set()
    return {
        int(entry["espn_player_id"])
        for entry in team.roster
        if entry.get("espn_player_id")
    }


def rosters_by_team(session: Session, league: League) -> dict[int, set[int]]:
    """Every team's roster, keyed by ESPN team id.

    Prefers ESPN's rosters, which stay correct through adds, drops and trades.
    Falls back to this season's draft results for a league that has drafted but
    whose rosters we have not imported -- and so that the screen is not blank
    mid-draft, when the draft log is the only record that exists.
    """
    rosters: dict[int, set[int]] = {}
    for team in league.teams:
        ids = team_roster_ids(league, team.espn_team_id)
        if ids:
            rosters[team.espn_team_id] = ids

    if rosters:
        return rosters

    picks = session.scalars(
        select(HistoricalDraftPick).where(
            HistoricalDraftPick.league_id == league.id,
            HistoricalDraftPick.season == league.season,
        )
    ).all()
    for pick in picks:
        if pick.espn_team_id is None or pick.espn_player_id is None:
            continue
        rosters.setdefault(int(pick.espn_team_id), set()).add(int(pick.espn_player_id))
    return rosters


def faab_remaining(league: League, session: Session) -> int:
    """FAAB left, if the league uses it. ESPN reports spend per team."""
    if not league.uses_faab:
        return 0
    team = my_team(session, league)
    if team is None:
        return league.acquisition_budget
    return league.acquisition_budget
