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
from ..projections.fantasypros import (
    SOURCE_KEY,
    FantasyProsClient,
    FantasyProsError,
    FantasyProsPlayer,
)
from ..projections.matching import Candidate, PlayerMatcher

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
    ours = session.scalars(select(Player).where(Player.season == league.season)).all()
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

    pool_size = session.scalar(
        select(func.count(Player.id)).where(Player.season == league.season)
    ) or 0
    coverage = (report["matched"] / pool_size) if pool_size else 0.0
    report["pool_size"] = pool_size
    report["coverage"] = round(coverage, 3)

    if coverage < MIN_COVERAGE:
        source.enabled = False
        report["enabled"] = False
        report["warning"] = (
            f"Only {report['matched']} of {pool_size} players were covered "
            f"({coverage:.0%}). Blending a partial source would value the top of "
            "each position on two providers and everyone else on one, which "
            "distorts the rankings, so it has been left switched off. "
            "FantasyPros' free tier truncates responses; a paid tier returns "
            "full ones."
        )
    else:
        source.enabled = True
        report["enabled"] = True

    session.flush()
    log.info(
        "FantasyPros import: %s received, %s matched, %.0f%% coverage, enabled=%s",
        report["received"],
        report["matched"],
        coverage * 100,
        report["enabled"],
    )
    return report
