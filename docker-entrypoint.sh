#!/bin/sh
set -eu

if [ "${REMOTE_DESKTOP:-false}" = "true" ]; then
    # Chromium's crashpad and NSS databases must have writable XDG paths on
    # the read-only root filesystem.  Without these, Debian ARM64 Chromium
    # exits with a misleading SIGTRAP before its CDP endpoint is ready.
    mkdir -p /tmp/.config /tmp/.cache /tmp/.local/share /tmp/runtime
    chmod 700 /tmp/runtime
    export XDG_CONFIG_HOME=/tmp/.config
    export XDG_CACHE_HOME=/tmp/.cache
    export XDG_DATA_HOME=/tmp/.local/share
    export XDG_RUNTIME_DIR=/tmp/runtime
    # This directory is created as root in the image.  Xorg requires it to
    # remain root-owned even though the display server runs as app.
    if [ ! -d /tmp/.X11-unix ]; then
        echo "missing root-owned /tmp/.X11-unix directory" >&2
        exit 1
    fi
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
    x11vnc -display :99 -localhost -forever -shared -nopw -noxdamage -threads -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    websockify --web=/usr/share/novnc 0.0.0.0:6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
    export DISPLAY=:99
    export CHROME_CRASHPAD_DISABLE=1
    exec python -m app --daemon
fi

exec xvfb-run --auto-servernum --server-args="-screen 0 1440x1000x24 -nolisten tcp" \
    python -m app --daemon
