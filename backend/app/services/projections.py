"""Importing projections from providers other than ESPN.

Blending is already supported: `ProjectionSource.weight` decides how much each
source counts, and the valuation engine re-scores every source's raw stats
under the league's own rules. All this has to do is get another provider's
numbers into `player_projections` attached to the right players.

Matching is the risk, not fetching. A projection stapled to the wrong player is
undetectable downstream, so the importer reports what it could not match rather
than quietly dropping it.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import League, Player, PlayerProjection, ProjectionSource
from ..projections.espn_public import SOURCE_KEY as PUBLIC_SOURCE_KEY
from ..projections.espn_public import fetch_projections
from ..projections.fantasypros import (
    SOURCE_KEY,
    FantasyProsClient,
    FantasyProsError,
    FantasyProsPlayer,
)
from ..projections.matching import Candidate, PlayerMatcher
from ..projections.sleeper import SOURCE_KEY as SLEEPER_SOURCE_KEY
from ..projections.sleeper import fetch_projections as fetch_sleeper
from .scope import player_filters

log = logging.getLogger(__name__)


def ensure_source(session: Session, key: str, label: str, weight: float = 1.0) -> ProjectionSource:
    source = session.scalars(
        select(ProjectionSource).where(ProjectionSource.key == key)
    ).first()
    if source is None:
        source = ProjectionSource(key=key, label=label, weight=weight, enabled=True)
        session.add(source)
        session.flush()
    return source


def store_projections(
    session: Session,
    league: League,
    provider_players: list[FantasyProsPlayer],
    source_key: str,
) -> dict:
    """Attach a provider's stat lines to our players.

    Returns a report including what failed to match, because a silent 60% match
    rate looks identical to a working import from the outside.
    """
    ours = session.scalars(select(Player).where(*player_filters(league))).all()
    matcher = PlayerMatcher(
        [
            Candidate(
                player_id=p.id,
                name=p.name,
                position=p.position,
                pro_team=p.pro_team or "",
            )
            for p in ours
        ]
    )

    existing = {
        row.player_id: row
        for row in session.scalars(
            select(PlayerProjection).where(PlayerProjection.source_key == source_key)
        ).all()
    }

    matched = 0
    matched_names: list[str] = []
    for entry in provider_players:
        if not entry.raw_stats:
            continue
        candidate = matcher.match(entry.name, entry.position, entry.pro_team)
        if candidate is None:
            continue

        row = existing.get(candidate.player_id)
        if row is None:
            row = PlayerProjection(player_id=candidate.player_id, source_key=source_key)
            session.add(row)
            existing[candidate.player_id] = row
        row.raw_stats = {k: float(v) for k, v in entry.raw_stats.items()}
        # Deliberately no source_points: another site's point total is scored
        # under their assumed rules, and storing it invites it being used.
        row.source_points = None
        row.projected_games = entry.projected_games
        matched += 1
        matched_names.append(f"{entry.name} ({entry.position})")

    session.flush()
    return {
        "source": source_key,
        "received": len(provider_players),
        "matched": matched,
        # Which players were covered, not just how many. With a truncated
        # provider response the answer is "the top of each position", and
        # seeing the names is what makes that obvious.
        "matched_sample": matched_names[:60],
        **matcher.report,
    }


#: Below this share of the player pool, a source is stored but left disabled.
#:
#: Blending a source that only covers the top of each position is worse than
#: not blending at all: those players get an average of two providers while
#: everyone else keeps one, so any systematic difference between the providers
#: becomes a step change in the middle of the board -- and VOR is measured
#: against that same distorted pool. FantasyPros' free tier truncates responses
#: to roughly ten players a position, which lands well under this.
MIN_COVERAGE = 0.5


def _has_other_projections(session: Session, league: League, source_key: str) -> bool:
    """Does any *other* source already project players in this season?"""
    return bool(
        session.scalar(
            select(func.count(PlayerProjection.id))
            .join(Player, Player.id == PlayerProjection.player_id)
            .where(
                *player_filters(league),
                PlayerProjection.source_key != source_key,
            )
        )
    )


def import_espn_public(
    session: Session,
    league: League,
    *,
    weight: float = 1.0,
    limit: int = 900,
) -> dict:
    """Pull ESPN's public projections and attach them by name.

    This is what makes a Yahoo league usable: Yahoo publishes no projections,
    so without a source like this every player is worth zero and the board is
    just an alphabet.

    The coverage gate below is deliberately *not* applied when this is the only
    source there is. That gate exists to stop a partial source distorting a
    blend -- but with nothing to blend against, a partial projection set beats
    no projections at all, so it is kept and the gap is reported instead.
    """
    source = ensure_source(session, PUBLIC_SOURCE_KEY, "ESPN projections (public)", weight)
    players = fetch_projections(league.season, limit=limit)
    report = store_projections(session, league, players, PUBLIC_SOURCE_KEY)
    _finish_source_import(session, league, source, report)
    return report


def import_sleeper(
    session: Session,
    league: League,
    *,
    weight: float = 1.0,
    week: int | None = None,
) -> dict:
    """Pull Sleeper's projections and attach them by name.

    Free and keyless, so this is the second opinion every league can have --
    whichever platform the league itself lives on. Like every other source, the
    raw stat lines are re-scored under this league's own rules.
    """
    source = ensure_source(session, SLEEPER_SOURCE_KEY, "Sleeper projections", weight)
    players = fetch_sleeper(league.season, week=week)
    report = store_projections(session, league, players, SLEEPER_SOURCE_KEY)
    _finish_source_import(session, league, source, report)
    return report


def _finish_source_import(
    session: Session, league: League, source: ProjectionSource, report: dict
) -> dict:
    """Coverage bookkeeping shared by every optional source.

    The gate exists so a source covering only the top of each position cannot
    quietly skew a blend. It is not applied when this is the only source there
    is: a partial projection set beats an empty board, and the gap is reported
    instead.
    """
    pool_size = session.scalar(
        select(func.count(Player.id)).where(*player_filters(league))
    ) or 0
    coverage = (report["matched"] / pool_size) if pool_size else 0.0
    report["pool_size"] = pool_size
    report["coverage"] = round(coverage, 3)

    sole_source = not _has_other_projections(session, league, source.key)
    if coverage < MIN_COVERAGE and not sole_source:
        source.enabled = False
        report["enabled"] = False
        report["warning"] = (
            f"Only {report['matched']} of {pool_size} players matched ({coverage:.0%}). "
            "Blending a partial source distorts the rankings, so it has been left "
            "switched off. You can turn it on anyway from the Projections card."
        )
    else:
        source.enabled = True
        report["enabled"] = True
        if coverage < MIN_COVERAGE:
            report["warning"] = (
                f"{report['matched']} of {pool_size} players matched ({coverage:.0%}). "
                "This is the only projection source available for this league, so it is "
                "switched on -- but players it did not cover have no projection and will "
                "rank at the bottom."
            )

    session.flush()
    log.info(
        "%s projections: %s received, %s matched, %.0f%% coverage, enabled=%s",
        source.key,
        report["received"],
        report["matched"],
        coverage * 100,
        report["enabled"],
    )
    return report


def set_source_state(
    session: Session,
    key: str,
    *,
    enabled: bool | None = None,
    weight: float | None = None,
) -> ProjectionSource:
    """Turn a projection source on or off, or change how much it counts.

    An explicit choice here overrides the automatic coverage gate: the gate is
    a default, not a veto, and someone who wants to rank on one source alone is
    allowed to.
    """
    source = session.scalars(
        select(ProjectionSource).where(ProjectionSource.key == key)
    ).first()
    if source is None:
        raise ValueError(f"No projection source named {key!r}.")
    if enabled is not None:
        source.enabled = bool(enabled)
    if weight is not None:
        source.weight = max(0.0, float(weight))
    session.flush()
    return source


def source_coverage(session: Session, league: League) -> dict[str, int]:
    """{source key: players it projects} for this league's pool.

    What the Projections card shows: "ESPN 612, Sleeper 588" answers "is this
    source actually doing anything?" in a way an on/off switch cannot.
    """
    rows = session.execute(
        select(PlayerProjection.source_key, func.count(PlayerProjection.id))
        .join(Player, Player.id == PlayerProjection.player_id)
        .where(*player_filters(league))
        .group_by(PlayerProjection.source_key)
    ).all()
    return {key: int(count) for key, count in rows}


def import_fantasypros(
    session: Session,
    league: League,
    api_key: str,
    *,
    week: int | str = "draft",
    weight: float = 1.0,
) -> dict:
    """Fetch and store FantasyPros projections. Requires the caller's own key."""
    if not api_key:
        raise FantasyProsError(
            "No FantasyPros API key configured. Add one on the League screen."
        )

    source = ensure_source(session, SOURCE_KEY, "FantasyPros projections", weight)
    client = FantasyProsClient(api_key=api_key, season=league.season)
    players = client.projections(week=week)
    report = store_projections(session, league, players, SOURCE_KEY)
    _finish_source_import(session, league, source, report)

    if report.get("warning") and not report["enabled"]:
        # Worth naming the usual cause: their free tier truncates each position
        # to roughly ten players, which lands well under the coverage gate.
        report["warning"] += (
            " FantasyPros' free tier truncates responses; a paid tier returns full ones."
        )
    return report
