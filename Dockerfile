# --- stage 1: build the frontend -------------------------------------------
FROM node:20-slim AS frontend

# Mirror the repo layout so vite.config.ts's relative outDir
# (../backend/app/static) resolves exactly as it does locally.
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# --- stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FWR_DATABASE_URL=sqlite:///./data/fantasy_war_room.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir .

COPY --from=frontend /build/backend/app/static ./backend/app/static

RUN mkdir -p /app/data && \
    useradd --create-home --uid 1000 fwr && \
    chown -R fwr:fwr /app
USER fwr

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

# Shell form so hosts that assign a port (Render, Railway, Fly) are honoured;
# `exec` keeps uvicorn as PID 1 so it still receives SIGTERM cleanly.
CMD exec uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "${PORT:-8000}"
