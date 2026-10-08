#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
python="$root/.venv/bin/python"
pidfile="${FORECASTLAB_PIDFILE:-$root/.forecastlab/server.pid}"
logfile="${FORECASTLAB_LOGFILE:-$root/.forecastlab/server.log}"
port="${FORECASTLAB_PORT:-18765}"
[[ "$pidfile" = /* ]] || pidfile="$root/$pidfile"
[[ "$logfile" = /* ]] || logfile="$root/$logfile"
if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] || ((10#$port < 1 || 10#$port > 65535)); then
  echo "Invalid FORECASTLAB_PORT: $port" >&2; exit 2
fi
port=$((10#$port))
case "${1:-status}" in start|stop|status) ;; *) echo "Usage: $0 start|stop|status" >&2; exit 2 ;; esac
umask 077
mkdir -p "$(dirname "$pidfile")"
exec 9>"$pidfile.lock"
flock -n 9 || { echo "Another server-control operation is in progress." >&2; exit 1; }

read_pid() {
  pid=""
  if [[ -f "$pidfile" ]]; then pid="$(cat "$pidfile")"; fi
}

owned_running() {
  local candidate="${1:-}"; local -a args=()
  [[ "$candidate" =~ ^[0-9]+$ ]] || return 1
  test "$(stat -c %u "/proc/$candidate" 2>/dev/null)" = "$(id -u)" || return 1
  test "$(readlink "/proc/$candidate/cwd" 2>/dev/null)" = "$root" || return 1
  mapfile -d '' -t args < "/proc/$candidate/cmdline" 2>/dev/null || return 1
  [[ ${#args[@]} = 10 && "${args[0]}" = "$python" && "${args[1]}" = -m &&
     "${args[2]}" = uvicorn && "${args[3]}" = app.api:app &&
     "${args[4]}" = --app-dir && "${args[5]}" = backend &&
     "${args[6]}" = --host && "${args[7]}" = 127.0.0.1 &&
     "${args[8]}" = --port && "${args[9]}" = "$port" ]]
}

healthy() {
  # The response must come from this PID's listener, not a competing process.
  "$python" - "$1" "$port" <<'PY'
import json
from pathlib import Path
import sys
import urllib.request

try:
    pid, port = int(sys.argv[1]), int(sys.argv[2])
    sockets = {fd.readlink().name for fd in Path(f"/proc/{pid}/fd").iterdir()}
    address = f"0100007F:{port:04X}"
    listeners = [line.split() for line in Path(f"/proc/{pid}/net/tcp").read_text().splitlines()[1:]]
    if not any(row[1] == address and row[3] == "0A" and f"socket:[{row[9]}]" in sockets
               for row in listeners):
        sys.exit(1)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
        body = json.load(response)
    if body.get("ok") is not True:
        sys.exit(1)
    print(json.dumps(body, ensure_ascii=False))
except (OSError, ValueError, KeyError):
    sys.exit(1)
PY
}

read_pid
case "${1:-status}" in
  start)
    if owned_running "$pid"; then
      if healthy "$pid" > /dev/null; then echo "ForecastLab is already running on 127.0.0.1:$port."; exit 0; fi
      echo "Owned ForecastLab process $pid is not healthy; inspect $logfile." >&2; exit 1
    fi
    if [[ -n "$pid" ]] && { [[ ! "$pid" =~ ^[0-9]+$ ]] || [[ -d "/proc/$pid" ]]; }; then
      echo "Refusing to replace PID record $pidfile: it does not identify this server." >&2; exit 1
    fi
    if ! "$python" - "$port" <<'PY'
import socket
import sys
try:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(sys.argv[1])))
except OSError as exc:
    print(f"Cannot start ForecastLab: loopback port {sys.argv[1]} is unavailable ({exc}).", file=sys.stderr)
    sys.exit(1)
PY
    then exit 1; fi
    mkdir -p "$(dirname "$logfile")"
    cd "$root"
    export NO_PROXY=127.0.0.1,localhost
    child=""; pending=""
    cleanup_start() {
      if [[ -n "$child" ]] && owned_running "$child"; then
        kill "$child" 2>/dev/null || true
        for _ in {1..20}; do owned_running "$child" || break; sleep .1; done
        if owned_running "$child"; then kill -KILL "$child" 2>/dev/null || true; fi
        wait "$child" 2>/dev/null || true
      fi
      if [[ -n "$pending" ]]; then rm -f -- "$pending"; fi
    }
    trap cleanup_start EXIT
    trap 'exit 1' INT TERM
    nohup "$python" -m uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port "$port" \
      9>&- >> "$logfile" 2>&1 < /dev/null &
    child=$!
    deadline=$((SECONDS + 20))
    while ((SECONDS < deadline)); do
      if ! kill -0 "$child" 2>/dev/null; then break; fi
      if owned_running "$child" && healthy "$child" > /dev/null && owned_running "$child"; then
        pending="$(mktemp "$pidfile.XXXXXX")"
        printf '%s\n' "$child" > "$pending"
        mv -f -- "$pending" "$pidfile"
        pending=""; child=""
        trap - EXIT INT TERM
        echo "ForecastLab started on 127.0.0.1:$port; inspect $logfile."
        exit 0
      fi
      sleep .25
    done
    echo "ForecastLab failed to start or become healthy; inspect $logfile. PID record preserved." >&2
    exit 1
    ;;
  stop)
    if owned_running "$pid"; then
      kill "$pid"
      for _ in {1..20}; do owned_running "$pid" || break; sleep .5; done
      if owned_running "$pid"; then
        kill -KILL "$pid"
        for _ in {1..20}; do owned_running "$pid" || break; sleep .1; done
      fi
      if owned_running "$pid"; then echo "ForecastLab process $pid did not stop." >&2; exit 1; fi
      if [[ "$(cat "$pidfile")" = "$pid" ]]; then rm -f -- "$pidfile"; fi
      echo "ForecastLab stopped."
    elif [[ -n "$pid" ]] && [[ -d "/proc/$pid" ]]; then
      echo "Refusing to stop PID $pid: it does not identify this server." >&2; exit 1
    else echo "No owned ForecastLab process found."; fi
    ;;
  status)
    if owned_running "$pid"; then healthy "$pid"; else echo "ForecastLab is stopped."; exit 1; fi
    ;;
esac
