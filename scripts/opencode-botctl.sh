#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/run"
PID_FILE="$RUN_DIR/opencode-bot.pid"
LOG_FILE="/tmp/opencode-bot.log"

mkdir -p "$RUN_DIR"

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

start() {
  if is_running; then
    echo "opencode-bot already running (pid $(cat "$PID_FILE"))"
    return 0
  fi

  cd "$ROOT_DIR"
  nohup python3 run.py >>"$LOG_FILE" 2>&1 < /dev/null &
  local pid=$!
  echo "$pid" >"$PID_FILE"
  sleep 1

  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "opencode-bot started (pid $pid)"
    return 0
  fi

  rm -f "$PID_FILE"
  echo "failed to start opencode-bot; check $LOG_FILE"
  return 1
}

stop() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "opencode-bot is not running"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..30}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      echo "opencode-bot stopped"
      return 0
    fi
    sleep 1
  done

  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
  echo "opencode-bot force stopped"
}

status() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    echo "opencode-bot running (pid $pid)"
    return 0
  fi
  echo "opencode-bot not running"
  return 1
}

restart() {
  stop
  start
}

show_log_hint() {
  echo "log file: $LOG_FILE"
}

usage() {
  cat <<'EOF'
Usage: scripts/opencode-botctl.sh <start|stop|restart|status|log>
EOF
}

cmd="${1:-}"
case "$cmd" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    restart
    ;;
  status)
    status
    ;;
  log)
    show_log_hint
    ;;
  *)
    usage
    exit 1
    ;;
esac
