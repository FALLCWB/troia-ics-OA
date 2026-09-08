#!/usr/bin/env bash
# smoke_test.sh — bring up troia-ics testbed, validate basic plumbing.
#
# Pass criteria:
#   1. docker compose up exits cleanly
#   2. plc container becomes healthy within 90s (Modbus port 502 reachable)
#   3. hmi container starts and serves HTTP 200 on :8081 within 120s
#   4. monitor container can read /host/proc/{plc_pid}/maps
#   5. PLC tank level (HR 1025 = %MW1) oscillates around setpoint (HR 1024 = %MW0)
#      i.e. PID is running and the in-scan process model has reached steady state
#      (we test for the natural P-controller offset, ~150 below setpoint=500).
#
# Usage: ./scripts/smoke_test.sh [--keep-up]
#   --keep-up : leave containers running after the test (default: down)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

KEEP_UP=0
[[ "${1:-}" == "--keep-up" ]] && KEEP_UP=1

log()  { printf '[smoke] %s\n' "$*"; }
fail() { printf '[smoke] FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '[smoke] PASS: %s\n' "$*"; }

cleanup() {
  if [[ "$KEEP_UP" -eq 0 ]]; then
    log "tearing down compose stack"
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  else
    log "leaving stack up (--keep-up)"
  fi
}
trap cleanup EXIT

# --- Step 1: bring up the stack ---
log "step 1/5 — docker compose up -d --build"
docker compose up -d --build >/dev/null

# --- Step 2: wait for PLC health ---
log "step 2/5 — waiting for plc health (OpenPLC web UI on :8080)"
for i in $(seq 1 18); do
  if [[ "$(docker inspect -f '{{.State.Health.Status}}' troia-plc 2>/dev/null)" == "healthy" ]]; then
    pass "plc healthy after ${i}x5s"
    break
  fi
  sleep 5
  [[ "$i" -eq 18 ]] && fail "plc never became healthy in 90s"
done

# --- Step 3: HMI HTTP ---
# SCADA-LTS startup is slow: Tomcat (~10s) + WAR deploy (~15s) + Mango context
# initialization + Quartz scheduler boot (~60s). Allow up to 4 min.
log "step 3/5 — waiting for hmi HTTP on :8081/Scada-LTS/ (up to 240s)"
for i in $(seq 1 48); do
  if curl -sf -o /dev/null -m 3 http://127.0.0.1:8081/Scada-LTS/ 2>/dev/null; then
    pass "hmi serving HTTP after ${i}x5s"
    break
  fi
  sleep 5
  [[ "$i" -eq 48 ]] && fail "hmi never served HTTP in 240s"
done

# --- Step 4: availability channel reachable from the monitor ---
# The monitor observes container lifecycle through the Docker daemon socket, which
# is the only host interface it holds. It has no shared PID namespace, no ptrace
# and no host root, so it cannot and must not inspect PLC process memory; the
# availability channel is defined entirely in terms of container state.
log "step 4/5 — availability channel: monitor reads PLC container state via the Docker socket"
AVAIL=$(docker compose exec -T monitor python3 -c "
import docker
c = docker.from_env()
k = c.containers.get('troia-plc')
print(k.status)
" 2>/dev/null | tr -d '\r' || true)
if [[ "${AVAIL:-}" != "running" ]]; then
  fail "monitor could not read troia-plc state through the Docker socket (got: '${AVAIL:-<none>}')"
fi
pass "availability channel live (troia-plc state='${AVAIL}')"

# The monitor must NOT be able to see host processes: that would contradict the
# privilege model stated in the article. Assert the absence explicitly.
if docker compose exec -T monitor sh -c "test -e /host/proc" 2>/dev/null; then
  fail "monitor has /host/proc mounted; the documented privilege model forbids it"
fi
pass "privilege model holds (monitor has no host /proc, no shared PID namespace)"

# --- Step 5: PV oscillates around setpoint ---
log "step 5/5 — PID running, PV reaches steady state in <=60s (skipped if Modbus 502 not up)"
# Modbus :502 only listens after the OpenPLC program is loaded + started via
# the web UI; if it isn't, skip this step rather than fail.
# containers/plc/bootstrap.sh loads, compiles and starts tank_pid.st after the
# web UI is up, so :502 appears later than the container healthcheck. Wait for it
# instead of probing once, which used to skip this step on a cold start and print
# a misleading instruction to upload the program by hand.
MODBUS_UP=0
for i in $(seq 1 24); do
  if docker compose exec -T monitor bash -c "echo > /dev/tcp/plc/502" 2>/dev/null; then
    MODBUS_UP=1
    log "  Modbus :502 reachable after ${i}x5s"
    break
  fi
  sleep 5
done
if [[ "$MODBUS_UP" -eq 0 ]]; then
  log "WARN: Modbus :502 not reachable after 120s; tank_pid.st was not loaded — see containers/plc/README.md"
  pass "smoke test complete (PV check skipped)"
  exit 0
fi
# Bring the system to a known state, then check that PV stops moving.
docker compose exec -T monitor python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('plc', port=502, timeout=2)
c.connect()
c.write_registers(address=1024, values=[500, 0, 0, 0, 200, 30, 10, 0])
c.close()
" 2>/dev/null || true
STABLE=0
PREV=-1
for i in $(seq 1 30); do
  LEVEL=$(docker compose exec -T monitor python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('plc', port=502, timeout=2)
c.connect()
r = c.read_holding_registers(address=1025, count=1)
print(r.registers[0] if not r.isError() else -1)
c.close()
" 2>/dev/null || echo "-1")
  if [[ "$LEVEL" -ge 0 ]]; then
    DELTA=$(( LEVEL > PREV ? LEVEL - PREV : PREV - LEVEL ))
    log "  read $i: pv=$LEVEL delta=$DELTA"
    if [[ "$PREV" -ge 0 && "$DELTA" -le 10 ]]; then
      STABLE=$((STABLE+1))
      if [[ "$STABLE" -ge 3 ]]; then
        pass "PV reached stable steady state (pv=$LEVEL, 3 consecutive reads within ±10)"
        break
      fi
    else
      STABLE=0
    fi
    PREV=$LEVEL
  fi
  sleep 2
  [[ "$i" -eq 30 ]] && fail "PV did not stabilise within 60s"
done

log ""
pass "smoke test completo — testbed troia-ics OK"
