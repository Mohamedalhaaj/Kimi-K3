#!/usr/bin/env bash
# Start Kimi Workspace 2 locally: FastAPI on :8787, Next.js on :3000.
#
# Idempotent — running it twice will not start two copies. Logs go to
# .local/logs, PIDs to .local/run, both gitignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${PORT:-8787}"
WEB_PORT="${WEB_PORT:-3000}"
RUN_DIR="$ROOT/.local/run"
LOG_DIR="$ROOT/.local/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

red()  { printf '\033[31m%s\033[0m\n' "$1"; }
grn()  { printf '\033[32m%s\033[0m\n' "$1"; }
dim()  { printf '\033[2m%s\033[0m\n' "$1"; }

# ---- preflight ------------------------------------------------------------
command -v uv   >/dev/null || { red "uv is required: brew install uv"; exit 1; }
command -v node >/dev/null || { red "node is required (v20+)"; exit 1; }

if [[ ! -f .env ]]; then
  red "No .env found."
  echo "  cp .env.example .env   then add your TOKENROUTER_API_KEY"
  exit 1
fi

if ! grep -qE '^TOKENROUTER_API_KEY=.+' .env; then
  red "TOKENROUTER_API_KEY is missing or empty in .env"
  exit 1
fi

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# ---- dependencies ---------------------------------------------------------
dim "Syncing API dependencies…"
(cd apps/api && uv sync --quiet)

if [[ ! -d apps/web/node_modules ]]; then
  dim "Installing web dependencies (first run only)…"
  (cd apps/web && npm install --no-audit --no-fund --silent)
fi

# ---- api ------------------------------------------------------------------
if port_busy "$API_PORT"; then
  dim "API already listening on :$API_PORT — leaving it alone."
else
  dim "Starting API on :$API_PORT…"
  # stdin is redirected from /dev/null and the child is disowned; otherwise it
  # inherits this script's stdin and keeps a calling pipe open, so the shell
  # never returns to the prompt.
  (
    cd apps/api
    nohup .venv/bin/python -m uvicorn kimi.main:app \
      --host 127.0.0.1 --port "$API_PORT" \
      >"$LOG_DIR/api.log" 2>&1 </dev/null &
    echo $! >"$RUN_DIR/api.pid"
    disown
  )
fi

# ---- web ------------------------------------------------------------------
if port_busy "$WEB_PORT"; then
  dim "Web already listening on :$WEB_PORT — leaving it alone."
else
  dim "Starting web on :$WEB_PORT…"
  (
    cd apps/web
    nohup npm run dev -- --port "$WEB_PORT" \
      >"$LOG_DIR/web.log" 2>&1 </dev/null &
    echo $! >"$RUN_DIR/web.pid"
    disown
  )
fi

# ---- wait for readiness ---------------------------------------------------
dim "Waiting for the API to become ready…"
for _ in $(seq 1 60); do
  if node -e "fetch('http://127.0.0.1:$API_PORT/readyz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" 2>/dev/null; then
    grn "API ready    http://127.0.0.1:$API_PORT"
    break
  fi
  sleep 0.5
done

for _ in $(seq 1 60); do
  if node -e "fetch('http://localhost:$WEB_PORT').then(()=>process.exit(0)).catch(()=>process.exit(1))" 2>/dev/null; then
    grn "Web ready    http://localhost:$WEB_PORT"
    break
  fi
  sleep 0.5
done

echo
grn "Kimi Workspace is running."
echo "  App    http://localhost:$WEB_PORT"
echo "  API    http://127.0.0.1:$API_PORT/docs"
echo "  Logs   .local/logs/{api,web}.log"
echo "  Stop   ./scripts/stop-local.sh"
