"""Tenancy, in one place.

Every league-shaped query has to be narrowed to the connection that owns it,
and a single missed filter is a data leak between two people's leagues -- the
kind that shows nothing in testing (one user, one connection, everything looks
right) and everything in production.

So the narrowing lives here rather than being retyped at seventeen call sites.
`Player.connection_id == league.connection_id` covers legacy rows too:
SQLAlchemy renders `IS NULL` when the value is None, which is exactly what a
pre-accounts database wants.
"""

from __future__ import annotations

from ..models import League, Player


def player_filters(league: League) -> tuple:
    """Conditions selecting the players belonging to this league's connection."""
    return (
        Player.connection_id == league.connection_id,
        Player.season == league.season,
    )
