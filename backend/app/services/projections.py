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

from sqlalchemy import select
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

    session.flush()
    return {
        "source": source_key,
        "received": len(provider_players),
        "matched": matched,
        **matcher.report,
    }


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

    ensure_source(session, SOURCE_KEY, "FantasyPros projections", weight)
    client = FantasyProsClient(api_key=api_key, season=league.season)
    players = client.projections(week=week)
    report = store_projections(session, league, players, SOURCE_KEY)
    log.info(
        "FantasyPros import: %s received, %s matched, %s unmatched",
        report["received"],
        report["matched"],
        report["unmatched_count"],
    )
    return report
