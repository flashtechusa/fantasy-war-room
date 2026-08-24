"""Fantasy War Room -- FastAPI application."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .api import (
    routes_admin,
    routes_auth,
    routes_automode_live,
    routes_config,
    routes_draft,
    routes_espn,
    routes_league,
    routes_players,
    routes_season,
    routes_sim,
    routes_system,
    routes_team,
)
from .api.deps import settings_dep
from .config import Settings, get_settings
from .db import get_db, init_db
from .espn.client import EspnConnectionError
from .espn.redaction import install_log_redaction
from .models import League, Player
from .services.importer import get_active_league
from .services.provider import build_espn_client

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Installed before anything can log: ESPN cookies are session credentials,
    # and the realistic way one escapes is an exception message carrying a
    # request URL, not a deliberate print.
    install_log_redaction()
    init_db()
    settings = get_settings()

    # Bootstrap the first account. There is no registration path, so without a
    # configured owner nobody could ever sign in.
    if settings.admin_username and settings.admin_password:
        from .db import session_scope
        from .services import auth as auth_service

        with session_scope() as session:
            created = auth_service.ensure_owner(
                session, settings.admin_username, settings.admin_password
            )
            if created is not None and created.username == settings.admin_username.strip().lower():
                log.info("Owner account available: %s", created.username)

    log.info(
        "Fantasy War Room ready (season=%s, demo=%s, espn_league=%s)",
        settings.espn_season,
        settings.demo_mode,
        settings.espn_league_id or "unset",
    )

    # Auto Mode is always installed but inert unless the install switch, the
    # account capability, and the user's own opt-in all line up.  The scheduler
    # waits before its first pass so startup/import work is never raced.
    from .services import automode_runner

    auto_mode_task = asyncio.create_task(
        automode_runner.scheduler_loop(), name="fantasy-war-room-auto-mode"
    )
    try:
        yield
    finally:
        auto_mode_task.cancel()
        with suppress(asyncio.CancelledError):
            await auto_mode_task


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

#: Reachable without signing in. Everything else under /api requires a session.
#: Health is here so uptime checks and the update script keep working; the
#: auth endpoints obviously must be, or nobody could sign in.
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/me",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/beta-request",
    # The browser extension runs on espn.com and holds no session cookie for
    # this app. It authenticates with a single-use pairing code instead, which
    # the endpoint itself verifies -- see routes_espn.connect_from_extension.
    "/api/espn/extension/connect",
    "/api/espn/extension/manifest-contract",
}


@app.middleware("http")
async def require_sign_in(request, call_next):
    """Gate the API in the application rather than at the web server.

    The web server's password prompt covered every path including the landing
    page, which made a public front page impossible and could not be styled or
    explained. Moving the check here lets the landing page be public while the
    data behind it is not -- and means the app is still protected if the proxy
    is ever reconfigured.
    """
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API_PATHS:
        from .services import auth as auth_service
        from .db import session_scope

        token = request.cookies.get(auth_service.SESSION_COOKIE)
        with session_scope() as session:
            user = auth_service.user_for_token(session, token)
        if user is None:
            return JSONResponse(
                status_code=401, content={"detail": "Sign in to continue."}
            )
    return await call_next(request)


app.include_router(routes_auth.router)
app.include_router(routes_admin.router)
app.include_router(routes_config.router)
app.include_router(routes_espn.router)
app.include_router(routes_system.router)
app.include_router(routes_league.router)
app.include_router(routes_players.router)
app.include_router(routes_draft.router)
app.include_router(routes_season.router)
app.include_router(routes_automode_live.router)
app.include_router(routes_team.router)
app.include_router(routes_sim.router)


def _build_fingerprint() -> dict:
    """What is actually deployed, readable without a terminal.

    The VPS installs from a zip, so there is no git checkout to interrogate and
    /api/system/version cannot answer. The built bundle's content hash is a
    perfect deployment fingerprint and is already sitting in index.html, and the
    auto-updater writes the commit it applied next to the database.
    """
    root = Path(__file__).resolve().parent
    bundle = ""
    try:
        index = (root / "static" / "index.html").read_text(encoding="utf-8")
        match = re.search(r"index-[A-Za-z0-9_-]+\.js", index)
        bundle = match.group(0) if match else ""
    except OSError:
        pass

    commit = ""
    try:
        settings = get_settings()
        path = settings.sqlite_path()
        if path is not None:
            stamp = path.parent / "last-applied-commit.txt"
            if stamp.exists():
                commit = stamp.read_text(encoding="utf-8").strip()[:8]
    except Exception:      # noqa: BLE001 - a version string is never worth a 500
        pass

    return {"bundle": bundle, "commit": commit}


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
                select(func.count())
                .select_from(Player)
                .where(Player.season == league.season, Player.source == league.source)
            )
            or 0
        )

    return {
        "status": "ok",
        "build": _build_fingerprint(),
        "season": settings.espn_season,
        "demo_mode": settings.demo_mode,
        "espn": {
            "league_id_configured": settings.espn_league_id is not None,
            "private_credentials_configured": settings.has_espn_credentials,
            "reachable_config": settings.can_reach_espn,
            "draft_source": settings.espn_draft_source,
        },
        "debug_screens": settings.debug_screens,
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
