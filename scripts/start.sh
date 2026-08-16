#!/usr/bin/env bash
# Fantasy War Room -- one-command setup and launch.
#
#   ./scripts/start.sh
#
# Creates the virtualenv, installs dependencies, rebuilds the UI if Node is
# available (a prebuilt UI ships in the repo, so Node is optional), prompts
# for ESPN credentials the first time, and starts the server.
#
# Safe to re-run: everything it does is idempotent.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PORT="${FWR_PORT:-8000}"

say()  { printf '\033[1;34m▸\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$1" >&2; exit 1; }

# --- python -----------------------------------------------------------------

PY=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$candidate"; break
    fi
  fi
done
[ -n "$PY" ] || die "Python 3.10+ is required but was not found on PATH."
say "Using $($PY --version)"

if [ ! -d .venv ]; then
  say "Creating virtualenv…"
  "$PY" -m venv .venv
fi
VENV_PY="$ROOT/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$ROOT/.venv/Scripts/python.exe"   # Git Bash on Windows
[ -x "$VENV_PY" ] || die "Virtualenv looks broken. Remove .venv and re-run."

say "Installing dependencies…"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -e .

# --- frontend ---------------------------------------------------------------
# A built UI is committed, so this is a refresh, not a requirement.

if command -v npm >/dev/null 2>&1; then
  say "Rebuilding the UI…"
  npm --prefix frontend install --silent >/dev/null 2>&1 || warn "npm install had warnings"
  if npm --prefix frontend run build >/dev/null 2>&1; then
    say "UI rebuilt."
  else
    warn "UI build failed — falling back to the version committed in the repo."
  fi
elif [ -f backend/app/static/index.html ]; then
  say "Node not found — using the prebuilt UI committed in the repo."
else
  die "No prebuilt UI found and Node is not installed. Install Node 18+ and re-run."
fi

# --- credentials ------------------------------------------------------------

if [ ! -f .env ]; then
  echo
  say "First run — let's set up your ESPN league."
  echo "  Find the league id in your league URL:"
  echo "    https://fantasy.espn.com/football/league?leagueId=<THIS>&seasonId=2026"
  echo
  read -r -p "  ESPN league ID: " LEAGUE_ID
  read -r -p "  Season [2026]: " SEASON
  SEASON="${SEASON:-2026}"
  echo
  echo "  Private league? Copy these from your browser cookies for fantasy.espn.com"
  echo "  (DevTools → Application → Cookies). Press Enter to skip for a public league."
  read -r -p "  SWID: " SWID
  read -r -p "  espn_s2: " S2

  umask 077
  {
    echo "FWR_ESPN_LEAGUE_ID=${LEAGUE_ID}"
    echo "FWR_ESPN_SEASON=${SEASON}"
    echo "FWR_ESPN_SWID=${SWID}"
    echo "FWR_ESPN_S2=${S2}"
    echo "FWR_DEMO_MODE=false"
    echo "FWR_DATABASE_URL=sqlite:///./data/fantasy_war_room.db"
  } > .env
  chmod 600 .env
  say "Wrote .env (git-ignored, permissions 600)."
fi

# --- verify -----------------------------------------------------------------

echo
say "Checking the ESPN connection…"
if "$VENV_PY" scripts/verify_espn.py; then
  echo
else
  echo
  warn "ESPN verification failed — see the message above."
  warn "Starting anyway; fix the credentials on the League tab and re-import."
  echo
fi

# --- launch -----------------------------------------------------------------

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "${LAN_IP:-}" ] || LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"

echo "──────────────────────────────────────────────────────────"
say "Fantasy War Room is starting."
echo "    On this machine : http://localhost:${PORT}"
[ -n "${LAN_IP:-}" ] && echo "    On your phone   : http://${LAN_IP}:${PORT}"
echo
echo "    Open the League tab and tap 'Import league' first."
echo "    Ctrl-C to stop."
echo "──────────────────────────────────────────────────────────"
echo

exec "$VENV_PY" -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "$PORT"
