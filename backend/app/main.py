"""Fantasy War Room -- FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api import (
    routes_config,
    routes_draft,
    routes_league,
    routes_players,
    routes_season,
    routes_sim,
    routes_system,
    routes_team,
    routes_yahoo,
)
from .api.deps import settings_dep
from .config import Settings, get_settings
from .db import get_db, init_db
from .espn.client import EspnConnectionError
from .models import League, Player
from .services.importer import get_active_league
from .services.provider import build_espn_client, build_yahoo_client
from .yahoo.client import YahooConnectionError
from .yahoo.oauth import YahooAuthError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    init_db()
    settings = get_settings()
    log.info(
        "Fantasy War Room ready (platform=%s, season=%s, demo=%s, league=%s)",
        settings.platform,
        settings.espn_season,
        settings.demo_mode,
        (settings.yahoo_league_id if settings.is_yahoo else settings.espn_league_id) or "unset",
    )
    yield


app = FastAPI(
    title="Fantasy War Room",
    version="0.1.0",
    description=(
        "AI-assisted ESPN fantasy football draft and season management. "
        "Every recommendation exposes the numbers behind it."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_config.router)
app.include_router(routes_yahoo.router)
app.include_router(routes_system.router)
app.include_router(routes_league.router)
app.include_router(routes_players.router)
app.include_router(routes_draft.router)
app.include_router(routes_season.router)
app.include_router(routes_team.router)
app.include_router(routes_sim.router)


@app.get("/api/health", tags=["system"])
def health(
    session: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
) -> dict:
    """Configuration + import status. Safe to expose: no secrets are returned."""
    league = get_active_league(session, settings)
    player_count = 0
    if league is not None:
        player_count = (
            session.scalar(
                select(func.count()).select_from(Player).where(Player.season == league.season)
            )
            or 0
        )

    return {
        "status": "ok",
        "season": settings.espn_season,
        "demo_mode": settings.demo_mode,
        "platform": settings.platform,
        "yahoo": {
            "league_id_configured": settings.yahoo_league_id is not None,
            "app_configured": settings.has_yahoo_app,
            "connected": settings.has_yahoo_credentials,
            "reachable_config": settings.can_reach_yahoo,
        },
        "espn": {
            "league_id_configured": settings.espn_league_id is not None,
            "private_credentials_configured": settings.has_espn_credentials,
            "reachable_config": settings.can_reach_espn,
        },
        "league_imported": league is not None,
        "league": (
            {
                "name": league.name,
                "season": league.season,
                "source": league.source,
                "team_count": league.team_count,
                "imported_at": league.imported_at,
            }
            if league
            else None
        ),
        "players_loaded": player_count,
        "leagues_stored": session.scalar(select(func.count()).select_from(League)) or 0,
    }


@app.get("/api/health/espn", tags=["system"])
def espn_health(settings: Settings = Depends(settings_dep)) -> dict:
    """Live ESPN connectivity probe -- used by the League Settings screen."""
    if settings.demo_mode:
        return {"connected": False, "demo_mode": True, "detail": "Demo mode is enabled."}
    if settings.espn_league_id is None:
        return {
            "connected": False,
            "demo_mode": False,
            "detail": "No league configured. Enter your league on the League tab, "
            "or set ESPN_LEAGUE_ID in the environment.",
        }
    try:
        client = build_espn_client(settings)
        return {"demo_mode": False, **client.check_connection()}
    except EspnConnectionError as exc:
        return {"connected": False, "demo_mode": False, "detail": str(exc)}


@app.get("/api/health/yahoo", tags=["system"])
def yahoo_health(settings: Settings = Depends(settings_dep)) -> dict:
    """Live Yahoo connectivity probe -- the Yahoo half of the League screen."""
    if settings.demo_mode:
        return {"connected": False, "demo_mode": True, "detail": "Demo mode is enabled."}
    if not settings.has_yahoo_app:
        return {
            "connected": False,
            "demo_mode": False,
            "detail": "No Yahoo app configured. Create one at developer.yahoo.com and paste "
            "its Client ID and Secret on the League tab.",
        }
    if not settings.has_yahoo_credentials:
        return {
            "connected": False,
            "demo_mode": False,
            "detail": "Not connected to Yahoo yet. Use 'Connect Yahoo' on the League tab.",
        }
    if settings.yahoo_league_id is None:
        return {
            "connected": False,
            "demo_mode": False,
            "detail": "Connected, but no league chosen. Pick one on the League tab.",
        }
    try:
        return {"demo_mode": False, **build_yahoo_client(settings).check_connection()}
    except (YahooConnectionError, YahooAuthError) as exc:
        return {"connected": False, "demo_mode": False, "detail": str(exc)}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Serve the built SPA, letting client-side routing handle unknown paths."""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

else:  # pragma: no cover - dev without a built frontend

    @app.get("/", include_in_schema=False)
    def dev_root() -> dict:
        return {
            "message": (
                "Frontend not built. Run `npm --prefix frontend run build`, or use the "
                "Vite dev server at http://localhost:5173."
            ),
            "docs": "/docs",
            "health": "/api/health",
        }
