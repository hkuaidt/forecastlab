#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
[[ "$(id -un)" = group8 ]] || { echo 'Only group8 may manage this supervisor.' >&2; exit 1; }
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
unit=forecastlab-supervisor.service
python="$root/.venv/bin/python"
case "${1:-status}" in
  install)
    umask 077
    mkdir -p "$HOME/.config/systemd/user" "$root/.forecastlab"
    cat > "$HOME/.config/systemd/user/$unit" <<EOF
[Unit]
Description=ForecastLab group8 API router and isolated GPU worker supervisor

[Service]
Type=simple
WorkingDirectory=$root
ExecStart=$python $root/deploy/supervisor.py run
Restart=always
RestartSec=5
TimeoutStopSec=90
KillMode=process
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF
    # Account-local linger only; no system service, driver, or global Docker edits.
    loginctl enable-linger group8
    systemctl --user daemon-reload
    systemctl --user enable --now "$unit"
    echo 'User supervisor enabled; healthy existing services are adopted without restart.'
    ;;
  pause)
    "$python" "$root/deploy/supervisor.py" pause
    systemctl --user stop "$unit"
    ;;
  resume)
    "$python" "$root/deploy/supervisor.py" resume
    systemctl --user start "$unit"
    ;;
  status)
    systemctl --user --no-pager status "$unit" || true
    "$python" "$root/deploy/supervisor.py" status
    ;;
  *) echo "Usage: $0 install|pause|resume|status" >&2; exit 2 ;;
esac
