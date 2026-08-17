#!/usr/bin/env bash
# Pull the latest code and restart the server. One command, safe to re-run.
#
#   ./scripts/update.sh
#
# The server runs with --reload, so after the first run a plain `git pull` is
# usually enough -- uvicorn notices the change and restarts itself.

set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${FWR_PORT:-8000}"
say() { printf '\033[1;34m▸\033[0m %s\n' "$1"; }

say "Pulling latest code…"
git pull --ff-only || {
  printf '\033[1;33m!\033[0m Pull failed (local changes?). Trying to continue.\n'
}

# Pick up any new dependencies without a full reinstall.
PY="python"
[ -x .venv/bin/python ] && PY=".venv/bin/python"
say "Syncing dependencies…"
"$PY" -m pip install --quiet -e . 2>/dev/null || true

say "Stopping any running server…"
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1

say "Starting on port ${PORT} (auto-reloads on future pulls)"
echo "──────────────────────────────────────────────────────────"
echo "  Your Codespace URL is unchanged. Open the League tab and"
echo "  tap 'Import league' to refresh your roster."
echo "──────────────────────────────────────────────────────────"
echo

exec "$PY" -m uvicorn app.main:app --app-dir backend \
  --host 0.0.0.0 --port "$PORT" --reload --reload-dir backend
