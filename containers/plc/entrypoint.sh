#!/bin/sh
# entrypoint.sh — start OpenPLC v3 webserver and trigger the auto-bootstrap
# script (uploads tank_pid.st, compiles, starts PLC) once the UI is up.

set -e

# Start OpenPLC's own startup script in the background.
cd /opt/openplc
./start_openplc.sh &
OPENPLC_PID=$!

# Wait briefly so its first log lines come first.
sleep 3

# Run bootstrap in the foreground; on success the script exits 0 and we
# `wait` on OpenPLC so the container stays alive.
/usr/local/bin/bootstrap.sh || echo "[entrypoint] bootstrap failed (continuing; OpenPLC remains up for manual recovery)"

wait $OPENPLC_PID
