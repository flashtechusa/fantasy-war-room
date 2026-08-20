"""Phase 1 -- League Settings screen."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..espn.client import EspnConnectionError, EspnNotConfigured
from ..models import (
    HistoricalDraftPick,
    League,
    Player,
    PlayerProjection,
    ProjectionSource,
    User,
)
from ..projections.fantasypros import SOURCE_KEY as FP_SOURCE_KEY
from ..projections.fantasypros import FantasyProsError
from ..projections.sleeper import SOURCE_KEY as SLEEPER_SOURCE_KEY
from ..projections.sleeper import SleeperError
from ..services import board as board_service
from ..services import projections as projection_service
from ..services import runtime_config
from ..services.importer import import_league, import_players
from ..services.provider import build_provider
from .deps import league_dep, settings_dep
from .routes_auth import require_user
from .serializers import serialize_history, serialize_league

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/league", tags=["league"])


class ImportRequest(BaseModel):
    include_players: bool = Field(
        default=True, description="Also refresh the player pool and projections."
    )
    include_history: bool = Field(
        default=True, description="Also pull draft results for this and prior seasons."
    )


@router.get("")
def read_league(
    league: League = Depends(league_dep),
) -> dict:
    """The imported league settings, exactly as the engine will use them."""
    return serialize_league(
        league, board_service.league_scoring(league), board_service.league_shape(league)
    )


@router.post("/import", status_code=status.HTTP_201_CREATED)
def run_import(
    payload: ImportRequest | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Connect to ESPN (or the demo provider) and import everything."""
    payload = payload or ImportRequest()
    try:
        # Inside the try: choosing a provider is the step that fails when the
        # account has no league connected, and that has to reach the user as
        # an instruction rather than a 500.
        provider = build_provider(settings)
        league = import_league(
            session,
            provider=provider,
            settings=settings,
            include_players=payload.include_players,
            include_history=payload.include_history,
        )
    except EspnNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except EspnConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface the real reason to the UI
        log.exception("League import failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {exc}",
        ) from exc

    board_service.clear_cache()
    player_count = session.scalar(
        select(func.count())
        .select_from(Player)
        .where(Player.season == league.season, Player.source == league.source)
    )

    return {
        "imported": True,
        "source": league.source,
        "league": serialize_league(
            league, board_service.league_scoring(league), board_service.league_shape(league)
        ),
        "players_imported": player_count,
    }


@router.post("/players/refresh")
def refresh_players(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Re-pull just the player pool (ADP and injuries move; league rules don't)."""
    try:
        count = import_players(session, league, build_provider(settings), settings)
    except EspnNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except EspnConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    board_service.clear_cache()
    return {"players_imported": count, "season": league.season}


@router.get("/history")
def read_history(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
) -> dict:
    """Previous draft results, when ESPN has them."""
    picks = session.scalars(
        select(HistoricalDraftPick).where(HistoricalDraftPick.league_id == league.id)
    ).all()
    return serialize_history(list(picks))


@router.get("/projection-sources")
def read_projection_sources(session: Session = Depends(get_db)) -> dict:
    """Registered projection providers and their blend weights."""
    sources = session.scalars(select(ProjectionSource)).all()
    return {
        "sources": [
            {
                "key": source.key,
                "label": source.label,
                "weight": source.weight,
                "enabled": source.enabled,
                "updated_at": source.updated_at,
            }
            for source in sources
        ]
    }


@router.post("/projections/fantasypros")
def import_fantasypros_projections(
    week: str = Query("draft", description="'draft' for season totals, or a week number"),
    weight: float = Query(1.0, ge=0.0, le=10.0),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Pull FantasyPros projections using this installation's own API key.

    Off unless a key is configured. The report includes what failed to match,
    because a half-matched import looks identical to a working one from the
    outside and would quietly skew the blend.
    """
    try:
        report = projection_service.import_fantasypros(
            session,
            league,
            api_key=settings.fantasypros_api_key or "",
            week=int(week) if str(week).isdigit() else "draft",
            weight=weight,
        )
    except FantasyProsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    session.commit()
    board_service.clear_cache()
    return report


# ---------------------------------------------------------------------------
# Projection source -- a per-user choice of ESPN / Sleeper / FantasyPros /
# consensus, plus each user's own FantasyPros key. Secrets are never returned.
# ---------------------------------------------------------------------------


class SleeperToggle(BaseModel):
    enabled: bool


class ProjectionModeRequest(BaseModel):
    mode: str = Field(..., description="espn | sleeper | fantasypros | consensus")


class FantasyProsKeyRequest(BaseModel):
    api_key: str | None = Field(
        None, description="Your FantasyPros API key. Empty/None clears the stored key."
    )
    import_now: bool = Field(
        True, description="After storing the key, fetch projections and report coverage."
    )


def _already_imported(session: Session, league: League, source_key: str) -> int:
    """How many of this league's players a stored source already covers."""
    return session.scalar(
        select(func.count(PlayerProjection.id))
        .join(Player, Player.id == PlayerProjection.player_id)
        .where(
            PlayerProjection.source_key == source_key,
            Player.season == league.season,
            Player.source == league.source,
        )
    ) or 0


def _source_coverage(session: Session, league: League, source_key: str) -> dict:
    """How many league players a stored source covers -- the honesty check."""
    matched = session.scalar(
        select(func.count(PlayerProjection.id))
        .join(Player, Player.id == PlayerProjection.player_id)
        .where(
            PlayerProjection.source_key == source_key,
            Player.season == league.season,
            Player.source == league.source,
        )
    ) or 0
    pool_size = session.scalar(
        select(func.count(Player.id)).where(
            Player.season == league.season, Player.source == league.source
        )
    ) or 0
    source = session.scalars(
        select(ProjectionSource).where(ProjectionSource.key == source_key)
    ).first()
    return {
        "imported": matched > 0,
        "players_matched": matched,
        "pool_size": pool_size,
        "coverage": round(matched / pool_size, 3) if pool_size else 0.0,
        "updated_at": source.updated_at if source else None,
    }


def _projection_status(session: Session, league: League, user: User) -> dict:
    """Everything the projection-source UI needs, secrets-free.

    Reports the chosen mode, per-source coverage, whether this user has stored
    their own FantasyPros key, and any warning the mode should surface (a source
    selected but not imported, or a partial FantasyPros key). The warning is what
    stops a thin source silently reading as ESPN underneath.
    """
    config = runtime_config.user_config(session, user)
    mode = runtime_config.resolve_projection_mode(config)
    # A key is usable if the user stored their own OR the install already has one
    # (the pre-existing install-level key keeps working -- no re-entry needed).
    own_key = runtime_config.has_fantasypros_key(config)
    effective = runtime_config.settings_for_user(session, user)
    fp_key_set = bool(effective.fantasypros_api_key)
    sleeper = _source_coverage(session, league, SLEEPER_SOURCE_KEY)
    fantasypros = _source_coverage(session, league, FP_SOURCE_KEY)
    fantasypros = {
        **fantasypros,
        "key_set": fp_key_set,
        "own_key": own_key,
        "key_source": "you" if own_key else ("install" if fp_key_set else None),
    }

    warnings: list[str] = []
    if mode == "sleeper" and not sleeper["imported"]:
        warnings.append("Sleeper is selected but not imported yet -- import it to use it.")
    if mode == "fantasypros":
        if not fp_key_set:
            warnings.append("FantasyPros is selected but you have not added your API key yet.")
        elif not fantasypros["imported"]:
            warnings.append("FantasyPros is selected but not imported yet -- import it to use it.")
        elif fantasypros["coverage"] < 0.5:
            warnings.append(
                f"FantasyPros covers only {fantasypros['coverage']:.0%} of the pool; "
                "uncovered players fall back to ESPN, so this is mostly ESPN underneath."
            )
    if mode == "consensus":
        have = [s for s, cov in (("Sleeper", sleeper), ("FantasyPros", fantasypros))
                if cov["imported"]]
        blended = ", ".join(["ESPN", *have]) if have else "ESPN only"
        warnings.append(f"Consensus is blending: {blended}. Import more sources to widen it.")

    return {
        "mode": mode,
        "modes": list(runtime_config.PROJECTION_MODES),
        # Legacy fields the older Sleeper UI read; derived from mode.
        "use_sleeper_projections": mode == "sleeper",
        "active_projection_source": mode,
        "sleeper": {
            **sleeper,
            "scope": "QB/RB/WR/TE re-scored under your rules; K and D/ST stay on the existing source.",
        },
        "fantasypros": fantasypros,
        "warnings": warnings,
    }


@router.get("/projections/status")
def projection_status(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """The signed-in user's projection source, per-source coverage, warnings."""
    return _projection_status(session, league, user)


@router.post("/projections/mode")
def set_projection_mode(
    payload: ProjectionModeRequest,
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Choose which projection source builds this user's board.

    Selecting a mode best-effort imports the sources it needs, so it just works:
    Sleeper needs no key; FantasyPros uses whatever key is already configured
    (the user's own or the existing install key -- no re-entry). Each import is
    non-fatal, and the status flags anything still missing. "espn" restores the
    native board.
    """
    try:
        mode = runtime_config.set_projection_mode(session, user, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if mode in ("sleeper", "consensus") and not _already_imported(session, league, SLEEPER_SOURCE_KEY):
        try:
            projection_service.import_sleeper(session, league)
            session.commit()
        except SleeperError:
            # Non-fatal: mode saved; status flags that Sleeper is not imported.
            session.rollback()

    if mode in ("fantasypros", "consensus") and not _already_imported(session, league, FP_SOURCE_KEY):
        fp_key = runtime_config.settings_for_user(session, user).fantasypros_api_key
        if fp_key:
            try:
                projection_service.import_fantasypros(session, league, api_key=fp_key)
                session.commit()
            except FantasyProsError:
                # Non-fatal: mode saved; status flags FantasyPros as not imported.
                session.rollback()

    board_service.clear_cache()
    return _projection_status(session, league, user)


@router.post("/projections/fantasypros/key")
def save_fantasypros_key(
    payload: FantasyProsKeyRequest,
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Store (or clear) this user's own FantasyPros key, encrypted, and test it.

    The raw key is never persisted or returned -- only Fernet ciphertext. When a
    key is provided and import_now is set, it is immediately used to fetch and
    store projections, and the coverage is returned so the user sees exactly how
    much of their roster FantasyPros actually covers before relying on it.
    """
    key_set = runtime_config.set_fantasypros_key(session, user, payload.api_key)
    report: dict | None = None
    if key_set and payload.import_now:
        try:
            report = projection_service.import_fantasypros(
                session, league, api_key=payload.api_key.strip()
            )
            session.commit()
            board_service.clear_cache()
        except FantasyProsError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
    return {"key_set": key_set, "import": report, "status": _projection_status(session, league, user)}


@router.get("/projections/sleeper")
def sleeper_status(
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Back-compat: the projection status, under the old Sleeper-only path."""
    return _projection_status(session, league, user)


@router.post("/projections/sleeper/import", status_code=status.HTTP_201_CREATED)
def import_sleeper_projections(
    week: int | None = Query(None, description="A week number, or omit for season totals."),
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Fetch Sleeper's raw component projections and store them (isolated).

    Stored disabled, so this never changes the default board. It is used only
    when this user selects Sleeper or Consensus. Re-runnable to refresh.
    """
    try:
        report = projection_service.import_sleeper(session, league, week=week)
    except SleeperError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    session.commit()
    board_service.clear_cache()
    return {**report, "status": _projection_status(session, league, user)}


@router.post("/projections/sleeper/toggle")
def toggle_sleeper_projections(
    payload: SleeperToggle,
    session: Session = Depends(get_db),
    league: League = Depends(league_dep),
    user: User = Depends(require_user),
) -> dict:
    """Back-compat: on == select Sleeper, off == restore ESPN.

    Superseded by POST /projections/mode. Kept so an older client still works.
    """
    if payload.enabled:
        already = session.scalar(
            select(func.count(PlayerProjection.id))
            .join(Player, Player.id == PlayerProjection.player_id)
            .where(
                PlayerProjection.source_key == SLEEPER_SOURCE_KEY,
                Player.season == league.season,
                Player.source == league.source,
            )
        ) or 0
        if not already:
            try:
                projection_service.import_sleeper(session, league)
                session.commit()
            except SleeperError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
                ) from exc

    runtime_config.set_projection_mode(session, user, "sleeper" if payload.enabled else "espn")
    board_service.clear_cache()
    return _projection_status(session, league, user)
