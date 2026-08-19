"""Phase 1 + Phase 2 -- import a league and its player pool into SQLite.

Imports are idempotent upserts keyed on (league_id, season) and
(season, espn_player_id), so re-running is safe and cheap.  Everything the
valuation engine needs is persisted, which means the app keeps working if ESPN
goes down between your import and your draft.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    HistoricalDraftPick,
    League,
    LeagueTeam,
    Player,
    PlayerProjection,
    PlayerWeeklyProjection,
    ProjectionSource,
    ScoringRule,
    utcnow,
)
from .provider import DataProvider, build_provider

log = logging.getLogger(__name__)

#: Ships with ESPN.  Add a row here plus an adapter to blend another source.
DEFAULT_PROJECTION_SOURCES = [
    {"key": "espn", "label": "ESPN season projections", "weight": 1.0},
    {"key": "demo", "label": "Synthetic demo projections", "weight": 1.0},
]


class ImportError_(RuntimeError):
    """Raised when an import cannot complete."""


def ensure_projection_sources(session: Session) -> None:
    existing = {row.key for row in session.scalars(select(ProjectionSource)).all()}
    for source in DEFAULT_PROJECTION_SOURCES:
        if source["key"] not in existing:
            session.add(ProjectionSource(**source, enabled=True))
    session.flush()


def get_active_league(session: Session, settings: Settings | None = None) -> League | None:
    """The league these settings point at, or None.

    Strict on purpose. This used to fall back to "whatever was imported most
    recently" when nothing matched, which was harmless with one user and a
    disclosure with two: a signed-in account that had configured no league of
    its own was handed somebody else's, with their roster and their waivers.

    No configured league now means no league, and the caller says so.
    """
    settings = settings or get_settings()

    if settings.demo_mode:
        # Demo mode is explicitly "show me anything"; that is its whole job.
        return session.scalars(
            select(League).order_by(League.imported_at.desc())
        ).first()

    if settings.espn_league_id is None:
        return None

    return session.scalars(
        select(League)
        .where(
            League.espn_league_id == settings.espn_league_id,
            League.season == settings.espn_season,
        )
        .order_by(League.imported_at.desc())
    ).first()


# ---------------------------------------------------------------------------
# League import
# ---------------------------------------------------------------------------


def import_league(
    session: Session,
    provider: DataProvider | None = None,
    settings: Settings | None = None,
    include_players: bool = True,
    include_history: bool = True,
) -> League:
    settings = settings or get_settings()
    provider = provider or build_provider(settings)

    ensure_projection_sources(session)

    data = provider.league_settings()
    league = _upsert_league(session, data)
    session.flush()

    _upsert_teams(session, league, provider.teams(), settings)
    _upsert_scoring_rules(session, league, data.get("scoring_rules", []))

    if include_history:
        _upsert_draft_history(session, league, provider)

    if include_players:
        import_players(session, league, provider, settings)

    session.commit()
    log.info(
        "Imported league %s (%s) from %s",
        league.name,
        league.season,
        data.get("source", "unknown"),
    )
    return league


def _upsert_league(session: Session, data: dict) -> League:
    league = session.scalars(
        select(League).where(
            League.espn_league_id == data["espn_league_id"],
            League.season == data["season"],
        )
    ).first()
    if league is None:
        league = League(
            espn_league_id=data["espn_league_id"],
            season=data["season"],
        )
        session.add(league)

    league.name = data.get("name") or ""
    league.team_count = int(data.get("team_count") or 10)
    league.scoring_type = data.get("scoring_type") or ""
    league.is_ppr = bool(data.get("is_ppr"))
    league.ppr_value = float(data.get("ppr_value") or 0.0)
    league.roster_slots = data.get("roster_slots") or {}
    league.bench_slots = int(data.get("bench_slots") or 0)
    league.ir_slots = int(data.get("ir_slots") or 0)
    league.draft_type = (data.get("draft_type") or "SNAKE").upper()
    league.draft_order = data.get("draft_order") or []
    league.draft_completed = bool(data.get("draft_completed"))
    league.keeper_count = int(data.get("keeper_count") or 0)
    league.waiver_type = data.get("waiver_type") or ""
    league.uses_faab = bool(data.get("uses_faab"))
    league.acquisition_budget = int(data.get("acquisition_budget") or 0)
    league.waiver_process_days = data.get("waiver_process_days") or []
    league.regular_season_weeks = int(data.get("regular_season_weeks") or 14)
    league.playoff_team_count = int(data.get("playoff_team_count") or 6)
    league.playoff_matchup_length = int(data.get("playoff_matchup_length") or 1)
    league.playoff_seed_tie_rule = data.get("playoff_seed_tie_rule") or ""
    league.trade_deadline = int(data.get("trade_deadline") or 0)
    league.veto_votes_required = int(data.get("veto_votes_required") or 0)
    league.source = data.get("source") or "espn"
    league.imported_at = utcnow()

    raw = dict(data.get("raw_settings") or {})
    raw["all_slot_counts"] = data.get("all_slot_counts") or {}
    raw["draft_date"] = data.get("draft_date")
    raw["draft_time_per_selection"] = data.get("draft_time_per_selection")
    raw["waiver_hours"] = data.get("waiver_hours")
    raw["acquisition_limit"] = data.get("acquisition_limit")
    league.raw_settings = raw
    return league


def _upsert_teams(
    session: Session, league: League, teams: list[dict], settings: Settings
) -> None:
    existing = {team.espn_team_id: team for team in league.teams}
    seen: set[int] = set()

    for data in teams:
        team_id = int(data["espn_team_id"])
        seen.add(team_id)
        team = existing.get(team_id)
        if team is None:
            team = LeagueTeam(league_id=league.id, espn_team_id=team_id)
            session.add(team)
            league.teams.append(team)

        team.name = data.get("name") or f"Team {team_id}"
        team.abbrev = data.get("abbrev") or ""
        team.owners = data.get("owners") or []
        team.logo_url = data.get("logo_url") or ""
        team.division_name = data.get("division_name") or ""
        team.draft_slot = data.get("draft_slot")
        team.wins = int(data.get("wins") or 0)
        team.losses = int(data.get("losses") or 0)
        team.ties = int(data.get("ties") or 0)
        team.roster = data.get("roster") or []
        team.is_mine = _is_my_team(data, settings)

    for team_id, team in existing.items():
        if team_id not in seen:
            session.delete(team)

    # If config didn't identify a team, fall back to the configured draft slot.
    if not any(t.is_mine for t in league.teams) and settings.my_draft_slot:
        for team in league.teams:
            if team.draft_slot == settings.my_draft_slot:
                team.is_mine = True
                break


def _is_my_team(data: dict, settings: Settings) -> bool:
    """Which of these teams is yours.

    Explicit configuration wins, then the SWID cookie: ESPN's member ids are
    the same brace-wrapped GUID as SWID, so the team you own identifies itself
    with no setup at all. Falling back to the cookie matters because without it
    a fresh import leaves every season screen empty and the reason is invisible.
    """
    if settings.my_team_id is not None:
        return int(data["espn_team_id"]) == settings.my_team_id

    if settings.my_team_name:
        target = settings.my_team_name.strip().lower()
        if (data.get("name") or "").strip().lower() == target:
            return True
        if any(target == (owner or "").strip().lower() for owner in data.get("owners") or []):
            return True

    if settings.espn_swid:
        swid = settings.espn_swid.strip().upper()
        if swid in {str(owner_id).strip().upper() for owner_id in data.get("owner_ids") or []}:
            return True

    return False


def _upsert_scoring_rules(session: Session, league: League, rules: list[dict]) -> None:
    existing = {rule.stat_id: rule for rule in league.scoring_rules}
    seen: set[int] = set()
    for data in rules:
        stat_id = int(data["stat_id"])
        seen.add(stat_id)
        rule = existing.get(stat_id)
        if rule is None:
            rule = ScoringRule(league_id=league.id, stat_id=stat_id)
            session.add(rule)
            league.scoring_rules.append(rule)
        rule.abbrev = data.get("abbrev") or ""
        rule.label = data.get("label") or ""
        rule.points = float(data.get("points") or 0.0)
        rule.points_overrides = data.get("points_overrides") or {}

    for stat_id, rule in existing.items():
        if stat_id not in seen:
            session.delete(rule)


def _upsert_draft_history(session: Session, league: League, provider: DataProvider) -> None:
    """This season's draft (if it happened) plus recent prior seasons."""
    by_season: dict[int, list[dict]] = {}

    current = provider.draft_results()
    if current:
        by_season[league.season] = current
    try:
        by_season.update(provider.previous_draft_results())
    except Exception as exc:  # pragma: no cover - best effort
        log.info("Skipping draft history: %s", exc)

    if not by_season:
        return

    for season, picks in by_season.items():
        session.query(HistoricalDraftPick).filter(
            HistoricalDraftPick.league_id == league.id,
            HistoricalDraftPick.season == season,
        ).delete(synchronize_session=False)
        for pick in picks:
            session.add(
                HistoricalDraftPick(
                    league_id=league.id,
                    season=season,
                    overall_pick=int(pick.get("overall_pick") or 0),
                    round_num=int(pick.get("round_num") or 0),
                    round_pick=int(pick.get("round_pick") or 0),
                    espn_team_id=pick.get("espn_team_id"),
                    team_name=pick.get("team_name") or "",
                    espn_player_id=pick.get("espn_player_id"),
                    player_name=pick.get("player_name") or "",
                    bid_amount=int(pick.get("bid_amount") or 0),
                    keeper=bool(pick.get("keeper")),
                )
            )


# ---------------------------------------------------------------------------
# Player import
# ---------------------------------------------------------------------------


def import_players(
    session: Session,
    league: League,
    provider: DataProvider | None = None,
    settings: Settings | None = None,
) -> int:
    """Refresh the player pool and its projections. Returns players imported."""
    settings = settings or get_settings()
    provider = provider or build_provider(settings)
    source_key = getattr(provider, "source", "espn")

    ensure_projection_sources(session)

    records = provider.player_pool(limit=settings.player_pool_size, ppr=league.is_ppr)
    if not records:
        raise ImportError_("The provider returned an empty player pool.")

    existing = {
        player.espn_player_id: player
        for player in session.scalars(
            select(Player).where(Player.season == league.season)
        ).all()
    }
    projections = {
        (proj.player_id, proj.source_key): proj
        for proj in session.scalars(select(PlayerProjection)).all()
    }
    weekly = {
        (row.player_id, row.source_key, row.week): row
        for row in session.scalars(select(PlayerWeeklyProjection)).all()
    }

    now = datetime.now(timezone.utc)
    imported = 0

    for record in records:
        player = existing.get(record.espn_player_id)
        if player is None:
            player = Player(
                season=league.season,
                espn_player_id=record.espn_player_id,
                name=record.name,
                position=record.position,
                source=source_key,
            )
            session.add(player)
            existing[record.espn_player_id] = player

        player.name = record.name
        player.position = record.position
        player.pro_team = record.pro_team
        player.eligible_slots = record.eligible_slots
        player.bye_week = record.bye_week
        player.projected_games = record.projected_games
        player.injury_status = record.injury_status
        player.injured = record.injured
        player.percent_owned = record.percent_owned
        player.percent_started = record.percent_started
        player.adp = record.adp
        player.espn_rank = record.espn_rank
        player.espn_position_rank = record.espn_position_rank
        player.espn_projected_points = record.espn_projected_points
        player.rookie = record.rookie
        player.availability = record.availability or player.availability
        player.on_team_id = record.on_team_id
        player.source = source_key
        player.updated_at = now

        if player.id is None:
            session.flush()  # need the surrogate key for the projection row

        projection = projections.get((player.id, source_key))
        if projection is None:
            projection = PlayerProjection(player_id=player.id, source_key=source_key)
            session.add(projection)
            projections[(player.id, source_key)] = projection
        projection.raw_stats = record.raw_stats
        projection.source_points = record.espn_projected_points
        projection.projected_games = record.projected_games
        projection.updated_at = now

        _store_weekly(session, weekly, player, source_key, record, now)

        imported += 1

    _assign_position_adp(session, league.season)
    session.commit()
    log.info("Imported %s players for season %s", imported, league.season)
    return imported


def _store_weekly(
    session: Session,
    weekly: dict,
    player: Player,
    source_key: str,
    record,
    now: datetime,
) -> None:
    """Upsert this player's per-week projections."""
    for week, stats in (record.weekly_stats or {}).items():
        key = (player.id, source_key, int(week))
        row = weekly.get(key)
        if row is None:
            row = PlayerWeeklyProjection(
                player_id=player.id, source_key=source_key, week=int(week)
            )
            session.add(row)
            weekly[key] = row
        row.raw_stats = stats
        row.source_points = (record.weekly_points or {}).get(week)
        row.updated_at = now


def import_free_agents(
    session: Session,
    league: League,
    provider: DataProvider | None = None,
    settings: Settings | None = None,
    week: int | None = None,
    limit: int = 300,
) -> int:
    """Refresh the waiver wire.

    Free agents move constantly in-season, so this is a separate, cheaper call
    than a full player import.
    """
    settings = settings or get_settings()
    provider = provider or build_provider(settings)
    source_key = getattr(provider, "source", "espn")
    week = week or provider.current_week

    records = provider.player_pool(
        limit=limit, ppr=league.is_ppr, week=week, free_agents_only=True
    )
    if not records:
        return 0

    existing = {
        p.espn_player_id: p
        for p in session.scalars(select(Player).where(Player.season == league.season)).all()
    }
    projections = {
        (proj.player_id, proj.source_key): proj
        for proj in session.scalars(select(PlayerProjection)).all()
    }
    weekly = {
        (row.player_id, row.source_key, row.week): row
        for row in session.scalars(select(PlayerWeeklyProjection)).all()
    }

    now = datetime.now(timezone.utc)
    for record in records:
        player = existing.get(record.espn_player_id)
        if player is None:
            player = Player(
                season=league.season,
                espn_player_id=record.espn_player_id,
                name=record.name,
                position=record.position,
                source=source_key,
            )
            session.add(player)
            existing[record.espn_player_id] = player
            session.flush()

        player.name = record.name
        player.position = record.position
        player.pro_team = record.pro_team
        player.eligible_slots = record.eligible_slots
        player.bye_week = record.bye_week
        player.injury_status = record.injury_status
        player.injured = record.injured
        player.percent_owned = record.percent_owned
        player.percent_started = record.percent_started
        player.availability = record.availability or "FREEAGENT"
        player.source = source_key
        player.on_team_id = record.on_team_id
        player.updated_at = now

        if record.raw_stats:
            projection = projections.get((player.id, source_key))
            if projection is None:
                projection = PlayerProjection(player_id=player.id, source_key=source_key)
                session.add(projection)
                projections[(player.id, source_key)] = projection
            projection.raw_stats = record.raw_stats
            projection.source_points = record.espn_projected_points
            projection.updated_at = now

        _store_weekly(session, weekly, player, source_key, record, now)

    session.commit()
    log.info("Refreshed %s free agents for week %s", len(records), week)
    return len(records)


def _assign_position_adp(session: Session, season: int) -> None:
    """Rank ADP within each position (WR14, RB7, ...)."""
    players = session.scalars(select(Player).where(Player.season == season)).all()
    by_position: dict[str, list[Player]] = {}
    for player in players:
        by_position.setdefault(player.position, []).append(player)
    for group in by_position.values():
        ranked = sorted(group, key=lambda p: (p.adp is None, p.adp or 9999, p.name))
        for idx, player in enumerate(ranked, start=1):
            player.position_adp = float(idx)
