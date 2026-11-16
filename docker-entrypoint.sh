#!/bin/sh
set -eu

if [ "${REMOTE_DESKTOP:-false}" = "true" ]; then
    Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp &
    fluxbox -display :99 >/tmp/fluxbox.log 2>&1 &
    x11vnc -display :99 -localhost -forever -shared -nopw -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
    export DISPLAY=:99
    exec python -m app --daemon
fi

exec xvfb-run --auto-servernum --server-args="-screen 0 1440x1000x24 -nolisten tcp" \
    python -m app --daemon
