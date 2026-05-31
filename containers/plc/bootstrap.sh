#!/bin/sh
# bootstrap.sh — auto-load tank_pid.st by direct SQLite insert + web-UI
# compile/start. Robust against OpenPLC web-UI HTML drift (the prior
# upload-program / upload-program-action approach was fragile).
#
# Idempotent: if Modbus :502 is already listening, exit clean.

set -eu

DB=/opt/openplc/webserver/openplc.db
ST_DIR=/opt/openplc/webserver/st_files
SRC="${OPENPLC_PROGRAM:-/programs/tank_pid.st}"
UI="${OPENPLC_UI:-http://localhost:8080}"
USER="${OPENPLC_USER:-openplc}"
PASS="${OPENPLC_PASS:-openplc}"
COOKIES=/tmp/openplc.cookies

log() { printf '[bootstrap] %s\n' "$*"; }

# Wait for webserver.
log "waiting for $UI ..."
for i in $(seq 1 60); do
  if curl -sf -o /dev/null --max-time 2 "$UI/login"; then
    log "webserver up after ${i}x2s"; break
  fi
  sleep 2
  [ "$i" -eq 60 ] && { log "FAIL: webserver did not respond in 120s"; exit 1; }
done

# Short-circuit: if Modbus :502 already listens, the program is already loaded.
if (echo > /dev/tcp/localhost/502) 2>/dev/null; then
  log "Modbus :502 already listening; nothing to do"
  exit 0
fi

# Copy .st into OpenPLC's st_files dir (keep stable filename for reuse).
log "copying $SRC -> $ST_DIR/tank_pid.st"
cp "$SRC" "$ST_DIR/tank_pid.st"

# Insert Programs row if missing.
existing=$(sqlite3 "$DB" "SELECT Prog_ID FROM Programs WHERE File='tank_pid.st' LIMIT 1;" || true)
if [ -z "$existing" ]; then
  log "inserting Programs DB row"
  sqlite3 "$DB" \
    "INSERT INTO Programs (Name, Description, File, Date_upload) VALUES ('tank_pid', 'auto-loaded via bootstrap.sh', 'tank_pid.st', strftime('%s', 'now'));"
fi

# Login.
log "logging in"
rm -f "$COOKIES"
curl -s -c "$COOKIES" -b "$COOKIES" \
  -d "username=$USER&password=$PASS" -o /dev/null "$UI/login"

# Compile.
log "compiling tank_pid.st"
curl -s -b "$COOKIES" "$UI/compile-program?file=tank_pid.st" -o /tmp/compile.html
for i in $(seq 1 40); do
  if curl -s -b "$COOKIES" "$UI/dashboard" | grep -q "Compilation finished"; then
    log "compile OK after ${i}x3s"; break
  fi
  if curl -s -b "$COOKIES" "$UI/dashboard" | grep -q "Compilation error\|compilation failed"; then
    log "FAIL: compile error"
    curl -s -b "$COOKIES" "$UI/compilation-logs" | head -80 >&2 || true
    exit 2
  fi
  sleep 3
  [ "$i" -eq 40 ] && { log "FAIL: compile timeout 120s"; exit 2; }
done

# Start PLC. Retry up to 3 times because /start_plc sometimes redirects
# without spawning the runtime on the first call.
for attempt in 1 2 3; do
  log "issuing start_plc (attempt $attempt)"
  curl -s -b "$COOKIES" "$UI/start_plc" -o /dev/null
  for i in $(seq 1 15); do
    if (echo > /dev/tcp/localhost/502) 2>/dev/null; then
      log "Modbus :502 listening after attempt $attempt, ${i}x2s"
      exit 0
    fi
    sleep 2
  done
  log "attempt $attempt did not bring :502 up, retrying"
done
log "FAIL: Modbus :502 not reachable after 3 start_plc attempts"
exit 3
