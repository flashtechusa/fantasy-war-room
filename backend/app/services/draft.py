"""Phase 5 -- live draft state.

Manual entry is the primary path: it always works, needs no cookies, and cannot
be broken by ESPN changing an endpoint mid-draft.  ESPN sync is an *optional*
overlay that reconciles into the same table, so switching it on or off never
loses picks you already entered.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..engine.draft_math import round_of, pick_in_round, slot_for_pick
from ..models import DraftPick, DraftSession, League, Player, utcnow
from .draft_diag import SyncAttempt, diagnostics
from .provider import build_espn_client

log = logging.getLogger(__name__)


class DraftError(RuntimeError):
    """A draft operation could not be completed."""


def resolve_my_slot(session: Session, league: League, settings: Settings | None = None) -> int:
    """Which draft slot is ours: config first, then the ESPN draft order."""
    settings = settings or get_settings()
    if settings.my_draft_slot:
        return max(1, min(settings.my_draft_slot, league.team_count))
    for team in league.teams:
        if team.is_mine and team.draft_slot:
            return team.draft_slot
    return 1


def get_or_create_session(
    session: Session, league: League, settings: Settings | None = None
) -> DraftSession:
    settings = settings or get_settings()
    draft = session.scalars(
        select(DraftSession)
        .where(DraftSession.league_id == league.id, DraftSession.is_active.is_(True))
        .order_by(DraftSession.created_at.desc())
    ).first()
    if draft is not None:
        return draft

    from .board import league_shape

    shape = league_shape(league)
    draft = DraftSession(
        league_id=league.id,
        name=f"{league.name} draft",
        my_draft_slot=resolve_my_slot(session, league, settings),
        team_count=league.team_count,
        rounds=shape.draft_rounds,
        draft_type=league.draft_type,
    )
    session.add(draft)
    session.commit()
    return draft


def drafted_payload(draft: DraftSession) -> list[dict]:
    return [
        {"espn_player_id": pick.espn_player_id, "overall_pick": pick.overall_pick}
        for pick in sorted(draft.picks, key=lambda p: p.overall_pick)
    ]


def my_player_ids(draft: DraftSession) -> set[int]:
    return {pick.espn_player_id for pick in draft.picks if pick.is_mine}


def next_open_pick(draft: DraftSession) -> int:
    used = {pick.overall_pick for pick in draft.picks}
    candidate = 1
    while candidate in used:
        candidate += 1
    return candidate


def record_pick(
    session: Session,
    draft: DraftSession,
    espn_player_id: int,
    overall_pick: int | None = None,
    source: str = "manual",
    league_season: int | None = None,
) -> DraftPick:
    """Mark a player as drafted."""
    if any(p.espn_player_id == espn_player_id for p in draft.picks):
        raise DraftError("That player has already been drafted.")

    overall = overall_pick or next_open_pick(draft)
    total = draft.team_count * draft.rounds
    if not 1 <= overall <= total:
        raise DraftError(f"Pick {overall} is outside this draft (1-{total}).")
    if any(p.overall_pick == overall for p in draft.picks):
        raise DraftError(f"Pick {overall} has already been recorded.")

    stmt = select(Player).where(Player.espn_player_id == espn_player_id)
    if league_season is not None:
        stmt = stmt.where(Player.season == league_season)
    player = session.scalars(stmt).first()
    if player is None:
        raise DraftError(
            f"Unknown player id {espn_player_id}. Re-import the player pool if this is new."
        )

    slot = slot_for_pick(overall, draft.team_count, draft.draft_type)
    pick = DraftPick(
        session_id=draft.id,
        overall_pick=overall,
        round_num=round_of(overall, draft.team_count),
        round_pick=pick_in_round(overall, draft.team_count),
        draft_slot=slot,
        espn_player_id=espn_player_id,
        player_name=player.name,
        position=player.position,
        pro_team=player.pro_team,
        is_mine=slot == draft.my_draft_slot,
        source=source,
    )
    session.add(pick)
    draft.picks.append(pick)
    draft.updated_at = utcnow()
    session.commit()
    return pick


def undo_last_pick(session: Session, draft: DraftSession) -> DraftPick | None:
    if not draft.picks:
        return None
    last = max(draft.picks, key=lambda p: p.overall_pick)
    draft.picks.remove(last)
    session.delete(last)
    draft.updated_at = utcnow()
    session.commit()
    return last


def remove_pick(session: Session, draft: DraftSession, overall_pick: int) -> bool:
    match = next((p for p in draft.picks if p.overall_pick == overall_pick), None)
    if match is None:
        return False
    draft.picks.remove(match)
    session.delete(match)
    draft.updated_at = utcnow()
    session.commit()
    return True


def reset_draft(session: Session, draft: DraftSession) -> None:
    for pick in list(draft.picks):
        session.delete(pick)
    draft.picks.clear()
    draft.updated_at = utcnow()
    session.commit()


def set_my_slot(session: Session, draft: DraftSession, slot: int) -> DraftSession:
    slot = max(1, min(slot, draft.team_count))
    draft.my_draft_slot = slot
    for pick in draft.picks:
        pick.is_mine = pick.draft_slot == slot
    draft.updated_at = utcnow()
    session.commit()
    return draft


def _local_player_names(session: Session, season: int) -> dict[int, str]:
    """ESPN player id -> name, from our own import.

    The live-draft endpoint carries ids only, which is exactly what keeps it
    small enough to poll. Names come from the pool we already have.
    """
    return {
        player.espn_player_id: player.name
        for player in session.scalars(select(Player).where(Player.season == season)).all()
    }


def sync_from_espn(
    session: Session, draft: DraftSession, league: League, settings: Settings | None = None
) -> dict:
    """Pull ESPN's draft board and reconcile it into our picks.

    Only *adds* picks we don't have; manual entries are never clobbered.  ESPN
    is polled at most once per `FWR_DRAFT_POLL_INTERVAL` seconds by the caller.

    Every attempt is recorded for the draft diagnostics screen, including
    failures -- during a draft, "ESPN stopped answering four minutes ago" is
    the single most useful thing the app can tell you, and it is invisible if
    only successes are kept.
    """
    settings = settings or get_settings()
    local_before = len(draft.picks)
    local_latest_before = max((p.overall_pick for p in draft.picks), default=0)

    attempt = SyncAttempt(
        local_pick_count=local_before,
        local_latest_pick=local_latest_before,
    )

    try:
        client = build_espn_client(settings)
        espn_picks = client.live_draft_picks(
            player_names=_local_player_names(session, league.season)
        )
    except Exception as exc:
        attempt.ok = False
        attempt.error = str(exc)
        diagnostics.record(draft.id, attempt)
        raise

    attempt.ok = True
    attempt.source = getattr(espn_picks, "source", "")
    attempt.endpoint = getattr(espn_picks, "endpoint", "")
    attempt.latency_ms = getattr(espn_picks, "latency_ms", 0.0)
    attempt.espn_draft_complete = bool(getattr(espn_picks, "drafted", False))
    attempt.espn_draft_in_progress = bool(getattr(espn_picks, "in_progress", False))
    attempt.library_pick_count = getattr(espn_picks, "library_pick_count", 0)
    attempt.direct_pick_count = getattr(espn_picks, "direct_pick_count", 0)
    attempt.espn_pick_count = len(espn_picks)
    attempt.espn_latest_pick = max(
        (p.get("overall_pick") or 0 for p in espn_picks), default=0
    )

    known_players = {pick.espn_player_id for pick in draft.picks}
    known_slots = {pick.overall_pick for pick in draft.picks}
    added = 0
    skipped: list[str] = []

    for pick in espn_picks:
        player_id = pick.get("espn_player_id")
        overall = pick.get("overall_pick")
        if not player_id or not overall:
            continue
        if player_id in known_players or overall in known_slots:
            continue
        try:
            record_pick(
                session,
                draft,
                espn_player_id=int(player_id),
                overall_pick=int(overall),
                source="espn",
                league_season=league.season,
            )
            known_players.add(player_id)
            known_slots.add(overall)
            added += 1
        except DraftError as exc:
            skipped.append(f"{pick.get('player_name', player_id)}: {exc}")

    draft.espn_sync_enabled = True
    draft.last_synced_at = utcnow()
    session.commit()

    attempt.new_picks = added
    attempt.local_pick_count = len(draft.picks)
    attempt.local_latest_pick = max((p.overall_pick for p in draft.picks), default=0)
    if skipped:
        attempt.error = "; ".join(skipped[:3])
    elif getattr(espn_picks, "errors", None):
        # One source failing while the other answered is not a sync failure,
        # but it is exactly what the diagnostics screen exists to surface.
        attempt.error = "; ".join(espn_picks.errors[:3])
    diagnostics.record(draft.id, attempt)

    return {
        "added": added,
        "total_espn_picks": len(espn_picks),
        "source": attempt.source,
        "skipped": skipped,
        "synced_at": draft.last_synced_at,
        # So the Live Draft screen can auto-follow a draft that is actually
        # running (and say so when ESPN does not report one in progress).
        "in_progress": attempt.espn_draft_in_progress,
        "complete": attempt.espn_draft_complete,
    }
