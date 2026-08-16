"""Persistence layer.

Everything imported from ESPN lands here so the app keeps working (and stays
fast) when ESPN is slow, rate-limiting, or simply unreachable mid-draft.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Phase 1 -- league import
# ---------------------------------------------------------------------------


class League(Base):
    """One row per (league_id, season). Everything else hangs off this."""

    __tablename__ = "leagues"
    __table_args__ = (UniqueConstraint("espn_league_id", "season", name="uq_league_season"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    espn_league_id: Mapped[int] = mapped_column(Integer, index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)

    name: Mapped[str] = mapped_column(String(200), default="")
    team_count: Mapped[int] = mapped_column(Integer, default=10)

    # Scoring
    scoring_type: Mapped[str] = mapped_column(String(50), default="")
    is_ppr: Mapped[bool] = mapped_column(Boolean, default=False)
    ppr_value: Mapped[float] = mapped_column(Float, default=0.0)

    # Roster
    roster_slots: Mapped[dict] = mapped_column(JSON, default=dict)  # {"QB": 1, "RB": 2, ...}
    bench_slots: Mapped[int] = mapped_column(Integer, default=6)
    ir_slots: Mapped[int] = mapped_column(Integer, default=1)

    # Draft
    draft_type: Mapped[str] = mapped_column(String(50), default="SNAKE")
    draft_order: Mapped[list] = mapped_column(JSON, default=list)  # [team_id, ...] by slot
    draft_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    keeper_count: Mapped[int] = mapped_column(Integer, default=0)

    # Waivers / acquisitions
    waiver_type: Mapped[str] = mapped_column(String(50), default="")
    uses_faab: Mapped[bool] = mapped_column(Boolean, default=False)
    acquisition_budget: Mapped[int] = mapped_column(Integer, default=0)
    waiver_process_days: Mapped[list] = mapped_column(JSON, default=list)

    # Playoffs / schedule
    regular_season_weeks: Mapped[int] = mapped_column(Integer, default=14)
    playoff_team_count: Mapped[int] = mapped_column(Integer, default=6)
    playoff_matchup_length: Mapped[int] = mapped_column(Integer, default=1)
    playoff_seed_tie_rule: Mapped[str] = mapped_column(String(60), default="")

    trade_deadline: Mapped[int] = mapped_column(Integer, default=0)
    veto_votes_required: Mapped[int] = mapped_column(Integer, default=0)

    # Provenance
    source: Mapped[str] = mapped_column(String(20), default="espn")  # espn | demo
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_settings: Mapped[dict] = mapped_column(JSON, default=dict)

    teams: Mapped[list["LeagueTeam"]] = relationship(
        back_populates="league", cascade="all, delete-orphan", order_by="LeagueTeam.espn_team_id"
    )
    scoring_rules: Mapped[list["ScoringRule"]] = relationship(
        back_populates="league", cascade="all, delete-orphan", order_by="ScoringRule.stat_id"
    )
    draft_picks: Mapped[list["HistoricalDraftPick"]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
        order_by="HistoricalDraftPick.overall_pick",
    )


class LeagueTeam(Base):
    __tablename__ = "league_teams"
    __table_args__ = (UniqueConstraint("league_id", "espn_team_id", name="uq_team_per_league"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)

    espn_team_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200), default="")
    abbrev: Mapped[str] = mapped_column(String(20), default="")
    owners: Mapped[list] = mapped_column(JSON, default=list)  # ["First Last", ...]
    logo_url: Mapped[str] = mapped_column(String(500), default="")
    division_name: Mapped[str] = mapped_column(String(100), default="")

    draft_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_mine: Mapped[bool] = mapped_column(Boolean, default=False)

    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    ties: Mapped[int] = mapped_column(Integer, default=0)

    # Current roster as [{"espn_player_id": .., "name": .., "position": .., "slot": ..}]
    roster: Mapped[list] = mapped_column(JSON, default=list)

    league: Mapped[League] = relationship(back_populates="teams")


class ScoringRule(Base):
    """One ESPN scoring item -- the exact rules our projections are scored with."""

    __tablename__ = "scoring_rules"
    __table_args__ = (UniqueConstraint("league_id", "stat_id", name="uq_scoring_stat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)

    stat_id: Mapped[int] = mapped_column(Integer)
    abbrev: Mapped[str] = mapped_column(String(30), default="")
    label: Mapped[str] = mapped_column(String(120), default="")
    points: Mapped[float] = mapped_column(Float, default=0.0)
    # ESPN per-position point overrides, keyed by position/slot id as a string.
    points_overrides: Mapped[dict] = mapped_column(JSON, default=dict)

    league: Mapped[League] = relationship(back_populates="scoring_rules")


class HistoricalDraftPick(Base):
    """A pick from a completed ESPN draft (this season's, or a previous one)."""

    __tablename__ = "historical_draft_picks"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    season: Mapped[int] = mapped_column(Integer, index=True)

    overall_pick: Mapped[int] = mapped_column(Integer)
    round_num: Mapped[int] = mapped_column(Integer)
    round_pick: Mapped[int] = mapped_column(Integer)
    espn_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_name: Mapped[str] = mapped_column(String(200), default="")
    espn_player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_name: Mapped[str] = mapped_column(String(200), default="")
    bid_amount: Mapped[int] = mapped_column(Integer, default=0)
    keeper: Mapped[bool] = mapped_column(Boolean, default=False)

    league: Mapped[League] = relationship(back_populates="draft_picks")


# ---------------------------------------------------------------------------
# Phase 2 -- player database
# ---------------------------------------------------------------------------


class Player(Base):
    """Season-scoped player record. Projection numbers live in PlayerProjection."""

    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("season", "espn_player_id", name="uq_player_season"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    espn_player_id: Mapped[int] = mapped_column(Integer, index=True)

    name: Mapped[str] = mapped_column(String(200), index=True)
    position: Mapped[str] = mapped_column(String(10), index=True)  # QB/RB/WR/TE/K/DST
    pro_team: Mapped[str] = mapped_column(String(10), default="FA")
    eligible_slots: Mapped[list] = mapped_column(JSON, default=list)

    bye_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    projected_games: Mapped[float] = mapped_column(Float, default=17.0)

    injury_status: Mapped[str] = mapped_column(String(40), default="ACTIVE")
    injured: Mapped[bool] = mapped_column(Boolean, default=False)

    percent_owned: Mapped[float] = mapped_column(Float, default=0.0)
    percent_started: Mapped[float] = mapped_column(Float, default=0.0)

    # Market
    adp: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_adp: Mapped[float | None] = mapped_column(Float, nullable=True)
    espn_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    espn_position_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ESPN's own applied projection, already scored under this league's rules.
    espn_projected_points: Mapped[float] = mapped_column(Float, default=0.0)

    rookie: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    projections: Mapped[list["PlayerProjection"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )


class ProjectionSource(Base):
    """Registry of projection providers.

    Phase 2 ships with `espn`; adding FantasyPros/Sleeper/a CSV import later is
    a matter of inserting a row here and writing an adapter that emits
    PlayerProjection rows.  Weights let you blend several sources.
    """

    __tablename__ = "projection_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerProjection(Base):
    """Raw season stat line from one source, for one player.

    We store the *raw stat totals* (passing yards, receptions, ...) rather than
    a pre-computed point total so the same projection can be re-scored under
    any league's rules.  That is what makes the rankings league-specific.
    """

    __tablename__ = "player_projections"
    __table_args__ = (UniqueConstraint("player_id", "source_key", name="uq_projection_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str] = mapped_column(String(50), index=True)

    # {stat_id (as str): value}
    raw_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    # Source's own point total, if it published one (used as a fallback).
    source_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_games: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    player: Mapped[Player] = relationship(back_populates="projections")


# ---------------------------------------------------------------------------
# Phase 5 -- live draft state
# ---------------------------------------------------------------------------


class DraftSession(Base):
    """A live (or mock) draft we are tracking."""

    __tablename__ = "draft_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(120), default="Live Draft")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    my_draft_slot: Mapped[int] = mapped_column(Integer, default=1)
    team_count: Mapped[int] = mapped_column(Integer, default=10)
    rounds: Mapped[int] = mapped_column(Integer, default=16)
    draft_type: Mapped[str] = mapped_column(String(20), default="SNAKE")

    espn_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    picks: Mapped[list["DraftPick"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="DraftPick.overall_pick"
    )


class DraftPick(Base):
    __tablename__ = "draft_picks"
    __table_args__ = (UniqueConstraint("session_id", "overall_pick", name="uq_pick_per_session"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("draft_sessions.id", ondelete="CASCADE"), index=True
    )

    overall_pick: Mapped[int] = mapped_column(Integer)
    round_num: Mapped[int] = mapped_column(Integer)
    round_pick: Mapped[int] = mapped_column(Integer)
    draft_slot: Mapped[int] = mapped_column(Integer)  # which slot made the pick

    espn_player_id: Mapped[int] = mapped_column(Integer, index=True)
    player_name: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[str] = mapped_column(String(10), default="")
    pro_team: Mapped[str] = mapped_column(String(10), default="")

    is_mine: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | espn
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[DraftSession] = relationship(back_populates="picks")
