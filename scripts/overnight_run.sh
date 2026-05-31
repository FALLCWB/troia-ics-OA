#!/usr/bin/env bash
# overnight_run.sh — fully automated overnight execution of the three-class
# killer-demo experiment (Class 1 already done, Class 2 + Class 3 new).
#
# Pipeline:
#   1. Archive existing avaliacao/* into avaliacao/_archive_<ts>/
#   2. docker compose down -v (wipe volumes so the rebuilt tank_pid.st loads)
#   3. docker compose up -d --build (rebuild + start)
#   4. Wait for PLC + HMI healthy (up to 5 min)
#   5. Smoke test: 1 iter each of the 4 new scenarios
#      Hard-fails if any *_on does not manifest or any *_off does manifest
#   6. Launch matrix: 15 iter × {provoke_seq_off, provoke_seq_on,
#      provoke_env_off, provoke_env_on} = 60 runs (~9-10 h)
#   7. Run analysis (killer_demo_analysis.py)
#   8. Print final summary
#
# All output logs to avaliacao/overnight_<ts>.log so the morning analysis
# can replay decisions made overnight.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TS="$(date +%Y%m%d_%H%M%S)"
LOG="avaliacao/overnight_${TS}.log"
mkdir -p avaliacao

# Number of iterations per scenario for the main batch. Default 15.
ITERS="${ITERS:-15}"
SCENARIOS="${SCENARIOS:-provoke_seq_off,provoke_seq_on,provoke_env_off,provoke_env_on}"

log() { printf '[overnight %s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }

log "==== OVERNIGHT RUN ${TS} ===="
log "ITERS=$ITERS  SCENARIOS=$SCENARIOS"
log "log file: $LOG"

# ---------------------------------------------------------------------------
# 1. Archive existing runs (so the new class-2/3 runs land in a clean tree).
# ---------------------------------------------------------------------------
ARCHIVE="avaliacao/_archive_${TS}"
mkdir -p "$ARCHIVE"
mv_count=0
for d in avaliacao/*/; do
  name="$(basename "$d")"
  case "$name" in
    _archive_*|_smoke|_plots) continue ;;
  esac
  mv "$d" "$ARCHIVE/" 2>/dev/null && mv_count=$((mv_count+1)) || true
done
log "archived $mv_count existing per-run directories to $ARCHIVE/"

# ---------------------------------------------------------------------------
# 2-3. Rebuild stack so the new tank_pid.st with 3 hidden triggers is loaded.
# ---------------------------------------------------------------------------
log "tearing down compose stack (down -v)"
docker compose down -v --remove-orphans 2>&1 | tee -a "$LOG" >/dev/null || true

log "rebuilding + bringing up stack (up -d --build) — first start can take 4-6 min for SCADA-LTS"
docker compose up -d --build 2>&1 | tee -a "$LOG" >/dev/null

# ---------------------------------------------------------------------------
# 4. Wait for stack health.
# ---------------------------------------------------------------------------
log "waiting for PLC healthy (Modbus 502)"
for i in $(seq 1 60); do
  health=$(docker inspect -f '{{.State.Health.Status}}' troia-plc 2>/dev/null || echo "starting")
  [ "$health" = "healthy" ] && { log "PLC healthy after ${i}x5s"; break; }
  sleep 5
  [ "$i" -eq 60 ] && { log "FAIL: PLC never healthy in 300s"; exit 1; }
done

log "waiting for tank_pid.st program to be loaded + running (Modbus :502 listens)"
for i in $(seq 1 60); do
  if docker compose exec -T monitor bash -c "echo > /dev/tcp/plc/502" 2>/dev/null; then
    log "Modbus :502 reachable after ${i}x5s"
    break
  fi
  sleep 5
  [ "$i" -eq 60 ] && {
    log "FAIL: Modbus :502 never reachable. tank_pid.st likely not loaded;"
    log "      see containers/plc/README.md for manual upload."
    log "      Halting — manual intervention required."
    exit 1
  }
done

log "waiting 30s additional settling time for PID to converge"
sleep 30

# ---------------------------------------------------------------------------
# 5. Smoke test: 1 iter each of the 4 new scenarios. Fail fast if any
#    treatment arm fails to manifest or any control arm spuriously manifests.
# ---------------------------------------------------------------------------
log ""
log "=== SMOKE TEST PHASE ==="
log "Running 1 iter each of the 4 new scenarios. Treatment arms must manifest;"
log "control arms must not."

bash "$REPO_ROOT/scripts/run_matrix.sh" \
  --configs single-plc \
  --scenarios provoke_seq_off,provoke_seq_on,provoke_env_off,provoke_env_on \
  --iters 1 2>&1 | tee -a "$LOG" >/dev/null

# Inspect outcomes.
log "smoke results:"
SMOKE_OK=1
for scenario in provoke_seq_off provoke_seq_on provoke_env_off provoke_env_on; do
  # Find the latest run dir for this scenario.
  dir=$(ls -1d avaliacao/*-${scenario}-i1 2>/dev/null | tail -1)
  if [ -z "$dir" ] || [ ! -f "$dir/integrity.summary.json" ]; then
    log "  $scenario: NO SUMMARY (run failed)"
    SMOKE_OK=0
    continue
  fi
  case "$scenario" in
    provoke_seq_*)
      key="trigger_seq_fired_max"
      manifested_key="manifested_seq"
      ;;
    provoke_env_*)
      key="trigger_env_fired_max"
      manifested_key="manifested_env"
      ;;
  esac
  val=$(python3 -c "import json; d=json.load(open('$dir/integrity.summary.json')); print(d.get('$key', 0))")
  mflag=$(python3 -c "import json; d=json.load(open('$dir/integrity.summary.json')); print(d.get('$manifested_key', False))")
  log "  $scenario: $key=$val manifested=$mflag  (dir: $(basename "$dir"))"
  case "$scenario" in
    *_on)
      if [ "$mflag" != "True" ]; then
        log "    ^^^ EXPECTED MANIFESTATION, got dormant"
        SMOKE_OK=0
      fi
      ;;
    *_off)
      if [ "$mflag" = "True" ]; then
        log "    ^^^ UNEXPECTED MANIFESTATION in control arm"
        SMOKE_OK=0
      fi
      ;;
  esac
done

if [ "$SMOKE_OK" -ne 1 ]; then
  log ""
  log "SMOKE TEST FAILED — halting before main batch."
  log "Inspect individual run dirs under avaliacao/*-i1 and tank_pid.st logic."
  log "DO NOT discard the smoke run data; it is the diagnostic surface."
  exit 1
fi

log ""
log "SMOKE TEST PASSED — launching main batch."

# ---------------------------------------------------------------------------
# 6. Main batch: ITERS iterations per scenario.
# ---------------------------------------------------------------------------
log ""
log "=== MAIN BATCH PHASE ==="
log "matrix: 1 config × 4 scenarios × $ITERS iter = $((4 * ITERS)) runs"
log "expected duration: $((4 * ITERS * 10)) min approx (~$((4 * ITERS / 6)) h)"

bash "$REPO_ROOT/scripts/run_matrix.sh" \
  --configs single-plc \
  --scenarios "$SCENARIOS" \
  --iters "$ITERS" 2>&1 | tee -a "$LOG" >/dev/null

log ""
log "MAIN BATCH COMPLETE — running per-class analysis."

# ---------------------------------------------------------------------------
# 7. Run analysis. Note: killer_demo_analysis.py is per-class aware and will
#    emit one verdict line per class. Class 1 has zero runs in this overnight
#    matrix (already finished), so it will be skipped gracefully.
# ---------------------------------------------------------------------------
docker compose exec -T monitor python3 /opt/troia/analysis/killer_demo_analysis.py 2>&1 | tee -a "$LOG"

# Copy summary out of container.
docker compose exec -T monitor cat /opt/troia/avaliacao/killer_demo_summary.json \
  > avaliacao/killer_demo_summary_overnight_${TS}.json

log ""
log "==== OVERNIGHT RUN COMPLETE ===="
log "summary: avaliacao/killer_demo_summary_overnight_${TS}.json"
log "log:     $LOG"
log "use these in the morning to refresh §V.G of the paper."
