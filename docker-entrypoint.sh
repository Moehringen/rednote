#!/bin/sh
set -eu

if [ "${REMOTE_DESKTOP:-false}" = "true" ]; then
    # The image runs as an unprivileged user.  Xvfb cannot create this
    # directory itself when it is absent, which makes Chromium exit with a
    # misleading crashpad/SIGTRAP error on some ARM64 hosts.
    mkdir -p /tmp/.X11-unix
    chmod 1777 /tmp/.X11-unix
    Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp &
    xvfb_pid=$!
    i=0
    while [ ! -S /tmp/.X11-unix/X99 ] && [ "$i" -lt 50 ]; do
        sleep 0.1
        i=$((i + 1))
    done
    if [ ! -S /tmp/.X11-unix/X99 ]; then
        echo "Xvfb did not create the :99 display socket" >&2
        kill "$xvfb_pid" 2>/dev/null || true
        exit 1
    fi
    fluxbox -display :99 >/tmp/fluxbox.log 2>&1 &
    x11vnc -display :99 -localhost -forever -shared -nopw -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
    export DISPLAY=:99
    export CHROME_CRASHPAD_DISABLE=1
    exec python -m app --daemon
fi

exec xvfb-run --auto-servernum --server-args="-screen 0 1440x1000x24 -nolisten tcp" \
    python -m app --daemon
