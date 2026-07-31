#!/usr/bin/env bash
# Stop the local API and web servers started by start-local.sh.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.local/run"

stop() {
  local name="$1" pidfile="$RUN_DIR/$1.pid"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      # Kill the process group so child workers go too.
      kill "$pid" 2>/dev/null || true
      pkill -P "$pid" 2>/dev/null || true
      echo "stopped $name (pid $pid)"
    else
      echo "$name was not running"
    fi
    rm -f "$pidfile"
  else
    echo "no pid file for $name"
  fi
}

stop api
stop web

# Belt and braces for dev servers that re-exec themselves.
pkill -f "uvicorn kimi.main:app" 2>/dev/null && echo "cleaned stray uvicorn" || true
pkill -f "next dev" 2>/dev/null && echo "cleaned stray next dev" || true

echo "done"
