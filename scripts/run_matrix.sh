#!/usr/bin/env bash
# run_matrix.sh — execute the full experimental matrix.
#
# Matrix:
#   CONFIGS:    single-plc, dual-plc, redundant-hmi   (3)
#   SCENARIOS:  baseline, malformed, mutate, replay   (4)
#   ITER:       1..10                                 (10)
#   Total runs: 120
#
# Each run:
#   1. docker compose down -v (clean state)
#   2. docker compose up -d   (with CONFIG-specific compose overlay)
#   3. wait for plc healthy
#   4. start monitor + availability + integrity + tcpdump in background
#   5. (warmup 60 s)
#   6. run scenario-specific attacker injection
#   7. (cool-down 30 s)
#   8. stop and collect logs into avaliacao/<run-id>/
#   9. run metrics.modbus_parser and metrics.scoring on the captured data
#
# Usage: ./scripts/run_matrix.sh [--dry-run] [--configs c1,c2] [--scenarios s1,s2] [--iters N]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
CONFIGS="single-plc"
SCENARIOS="baseline,malformed,mutate,replay"
ITERS=10
WARMUP_SEC=60
ATTACK_SEC=180
COOLDOWN_SEC=30

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=1; shift;;
    --configs)   CONFIGS="$2"; shift 2;;
    --scenarios) SCENARIOS="$2"; shift 2;;
    --iters)     ITERS="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

log() { printf '[matrix] %s\n' "$*"; }

run_one() {
  local config="$1" scenario="$2" iter="$3"
  local stamp; stamp="$(date -u +%Y-%m-%dT%H%M%S)"
  local run_id="${stamp}-${config}-${scenario}-i${iter}"
  local run_dir="$REPO_ROOT/avaliacao/${run_id}"
  mkdir -p "$run_dir"
  log "============================================================"
  log "RUN ${run_id}"
  log "============================================================"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run: skipping execution; would write to $run_dir)"
    return 0
  fi

  # 1+2. Bring up the configuration-specific overlay. We always re-apply the
  # compose configuration with the overlay for the current config — Docker
  # Compose only creates/recreates services that differ from the running
  # state, so this is idempotent for the base services (plc, hmi, etc.) and
  # ensures overlay-specific services (e.g. plc2, hmi2) come up when the
  # config requires them. We deliberately do NOT `docker compose down`
  # between iterations to preserve the loaded PLC program in plc-data.
  CONFIG="$config" SCENARIO="$scenario" \
    docker compose -f docker-compose.yml \
                   -f "compose-overlays/${config}.yml" up -d --build 2>&1 | tail -3
  # Wait for plc healthy.
  for i in $(seq 1 24); do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' troia-plc 2>/dev/null)" == "healthy" ]] && break
    sleep 5
  done
  # Verify Modbus is actually listening — if not (because the container was
  # recreated and OpenPLC v3 doesn't auto-resume the runtime), try to recover
  # by clicking /start_plc via the web UI. tank_pid.st persists in the
  # plc-data Docker volume, so only the runtime needs to be re-launched.
  if ! docker compose exec -T monitor bash -c "echo > /dev/tcp/plc/502" 2>/dev/null; then
    log "Modbus :502 not listening — attempting auto-recovery via /start_plc"
    docker compose exec -T attacker python3 -c "
import urllib.request, urllib.parse, http.cookiejar, time, socket
for _ in range(30):
    try:
        urllib.request.urlopen('http://plc:8080/login', timeout=2); break
    except Exception:
        time.sleep(2)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open('http://plc:8080/login',
        data=urllib.parse.urlencode({'username':'openplc','password':'openplc'}).encode())
op.open('http://plc:8080/start_plc')
for _ in range(30):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect(('plc', 502)); s.close(); break
    except Exception:
        time.sleep(2)
" 2>/dev/null || true
    # Re-verify; abort only if recovery failed.
    if ! docker compose exec -T monitor bash -c "echo > /dev/tcp/plc/502" 2>/dev/null; then
      log "ABORT: Modbus :502 still not reachable after auto-recovery. Load tank_pid.st via the OpenPLC UI manually and rerun."
      return 1
    fi
    log "auto-recovery succeeded; continuing"
  fi

  # 4. Background instrumentation.
  # The two monitor collectors (availability, integrity) run inside the monitor
  # container — they observe the testbed from an externally-isolated namespace,
  # which is what the paper's multi-channel independence claim requires.
  # The tcpdump capture runs inside the dedicated `tcpdump` sidecar, which
  # shares the PLC's network namespace and therefore sees every Modbus PDU
  # arriving on the PLC's veth interface (attacker -> plc, hmi -> plc).
  #
  # availability gets the set of containers active in *this* config — single-plc
  # has plc+hmi, dual-plc adds plc2, redundant-hmi adds hmi2.
  local avail_containers=("troia-plc" "troia-hmi")
  case "$config" in
    dual-plc)        avail_containers+=("troia-plc2") ;;
    redundant-hmi)   avail_containers+=("troia-hmi2") ;;
  esac
  docker compose exec -d monitor python3 -m metrics.availability \
    --containers "${avail_containers[@]}" --out "/opt/troia/avaliacao/${run_id}/availability.jsonl" \
    --interval 1.0 --duration $((WARMUP_SEC + ATTACK_SEC + COOLDOWN_SEC))
  docker compose exec -d monitor python3 -m metrics.integrity \
    --host plc --out "/opt/troia/avaliacao/${run_id}/integrity.jsonl" \
    --interval 0.1 --duration $((WARMUP_SEC + ATTACK_SEC + COOLDOWN_SEC)) --warmup 10
  docker compose exec -d tcpdump tcpdump -i any -w "/opt/troia/avaliacao/${run_id}/capture.pcap" "port 502"

  # Confirm collectors are actually running (poll up to 10 s).
  for spec in "metrics.availability:monitor" "metrics.integrity:monitor" "tcpdump:tcpdump"; do
    collector="${spec%:*}"
    container="${spec#*:}"
    for _ in $(seq 1 20); do
      if docker compose exec -T "$container" pgrep -f "$collector" >/dev/null 2>&1; then
        break
      fi
      sleep 0.5
    done
  done

  # 4.5. Reset PLC state and let it settle BEFORE the detector's warmup window
  # starts collecting. The OpenPLC runtime persists internal state in-process;
  # we cannot zero the integral term remotely, but we can pin the setpoint and
  # tunings and then wait long enough for the PID to reach its steady-state
  # offset (~10 s after a write). Without this pre-settle, the initial PV
  # transient pollutes the IsoForest baseline and inflates the false-positive
  # rate on subsequent windows.
  docker compose exec -T monitor python3 -c "
from pymodbus.client import ModbusTcpClient
c = ModbusTcpClient('plc', port=502, timeout=2)
c.connect()
c.write_registers(address=1024, values=[500])
c.write_registers(address=1028, values=[200, 30, 10])
# Clear the hidden-trigger context markers for all three trigger classes so
# a previous provoke_* run does not leak its activation state into the
# current run.
#   Class 1: context_a==0 AND context_b==0 -> resets trigger_fired
#   Class 2: magic_seq==0 -> within 2 scans (last_seq=0) resets trigger_seq_fired
#   Class 3: sp==500 AND pv in [300,400] -> auto-resets trigger_env_fired
#            (the setpoint write above + the 20s pre-settle do this)
c.write_registers(address=1032, values=[0, 0])
c.write_register(address=1035, value=0)
c.close()
" 2>/dev/null || log "WARN: failed to reset PLC setpoint/tunings"
  log "pre-settle 20s (PID converges to steady state before warmup window)"
  sleep 20

  # 5. Warmup.
  log "warmup ${WARMUP_SEC}s"
  sleep "$WARMUP_SEC"

  # 6. Attack phase.
  case "$scenario" in
    baseline)
      log "scenario=baseline (no attack, ${ATTACK_SEC}s nominal traffic)"
      sleep "$ATTACK_SEC"
      ;;
    malformed)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        malformed --plc plc:502 --count 30 --interval $((ATTACK_SEC/30))
      ;;
    mutate)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        mutate --target troia-plc
      sleep "$ATTACK_SEC"
      ;;
    replay)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        replay --plc plc:502 --capture-sec 30 --max-replays 20 --interval $((ATTACK_SEC/20))
      ;;
    provoke_on)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        provoke_on --plc plc:502 --hold-sec "$ATTACK_SEC" --refresh-sec 2.0
      ;;
    provoke_off)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        provoke_off --hold-sec "$ATTACK_SEC"
      ;;
    provoke_seq_on)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        provoke_seq_on --plc plc:502 --hold-sec "$ATTACK_SEC"
      ;;
    provoke_seq_off)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        provoke_seq_off --hold-sec "$ATTACK_SEC"
      ;;
    provoke_env_on)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        provoke_env_on --plc plc:502 --hold-sec "$ATTACK_SEC" --refresh-sec 2.0
      ;;
    provoke_env_off)
      docker compose exec -T attacker python3 -m attacker.inject \
        --events-path "/opt/troia/avaliacao/${run_id}/chaos_events.jsonl" \
        provoke_env_off --hold-sec "$ATTACK_SEC"
      ;;
  esac

  # 7. Cool-down.
  log "cool-down ${COOLDOWN_SEC}s"
  sleep "$COOLDOWN_SEC"

  # 7.5. Post-mutate recovery. The mutate scenario restarts the PLC container;
  # OpenPLC v3 does not auto-resume the runtime after a restart. We also
  # need to restart the tcpdump sidecar — when the PLC container's network
  # namespace was destroyed by docker restart, the sidecar (which shares it)
  # was left bound to a now-defunct interface and is no longer capturing.
  if [[ "$scenario" == "mutate" ]]; then
    log "post-mutate recovery: bringing OpenPLC runtime + tcpdump back up"
    # The tcpdump sidecar shares plc's namespace, so when plc was restarted
    # the sidecar's interface attachment broke. Recreate the sidecar AND
    # relaunch tcpdump writing to capture_post.pcap (the modbus parser
    # consumes both files).
    docker compose up -d --force-recreate tcpdump >/dev/null 2>&1 || true
    # Give the sidecar a moment to come up before relaunching capture.
    sleep 2
    docker compose exec -d tcpdump tcpdump -i any -w "/opt/troia/avaliacao/${run_id}/capture_post.pcap" "port 502" 2>/dev/null || true
    docker compose exec -T attacker python3 -c "
import urllib.request, urllib.parse, http.cookiejar, time, socket
for _ in range(60):
    try:
        urllib.request.urlopen('http://plc:8080/login', timeout=2)
        break
    except Exception:
        time.sleep(2)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.open('http://plc:8080/login',
        data=urllib.parse.urlencode({'username':'openplc','password':'openplc'}).encode())
op.open('http://plc:8080/start_plc')
for _ in range(30):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect(('plc', 502))
        s.close(); break
    except Exception:
        time.sleep(2)
" 2>/dev/null || log "WARN: post-mutate recovery failed; subsequent iterations may abort"
  fi

  # 8. Stop background collectors (do NOT tear containers down — preserves
  # the running PLC for the next iteration).
  docker compose exec -T monitor pkill -f metrics.availability 2>/dev/null || true
  docker compose exec -T monitor pkill -f metrics.integrity 2>/dev/null || true
  docker compose exec -T tcpdump pkill -f tcpdump 2>/dev/null || true
  # Copy outputs from monitor's mounted avaliacao to host.
  # (Already on host — mount is bind, so files are immediately visible.)

  # 9. Post-processing (runs inside the monitor container; scapy/numpy come
  # from the container image, not the host).
  local in_run_dir="/opt/troia/avaliacao/${run_id}"
  # The mutate scenario can produce two pcap parts: capture.pcap (pre-restart)
  # and capture_post.pcap (post-recovery). Concatenate any non-empty parts
  # with `mergecap`-equivalent semantics: scapy's PcapReader handles them
  # serially via the --pcap argument repeated; here we pick the first
  # non-empty file or merge them.
  local pcaps=()
  [[ -s "$run_dir/capture.pcap" ]] && pcaps+=("${in_run_dir}/capture.pcap")
  [[ -s "$run_dir/capture_post.pcap" ]] && pcaps+=("${in_run_dir}/capture_post.pcap")
  if [[ ${#pcaps[@]} -gt 0 ]]; then
    docker compose exec -T monitor python3 -m metrics.modbus_parser \
      --pcap "${pcaps[@]}" \
      --out "${in_run_dir}/modbus.summary.json" \
      --windows-out "${in_run_dir}/modbus_windows.jsonl" \
      --window-sec 5 || true
  fi
  if [[ -s "$run_dir/integrity.jsonl" ]]; then
    docker compose exec -T monitor python3 -m monitor.detector \
      --run-dir "${in_run_dir}/" \
      --window-sec 5 --baseline-sec "$WARMUP_SEC" \
      --out "${in_run_dir}/anomalies.jsonl" || true
  fi
  # Tolerance >= half a window so an event firing mid-window can match.
  if [[ -s "$run_dir/chaos_events.jsonl" && -s "$run_dir/anomalies.jsonl" ]]; then
    docker compose exec -T monitor python3 -m metrics.scoring \
      --events "${in_run_dir}/chaos_events.jsonl" \
      --anomalies "${in_run_dir}/anomalies.jsonl" \
      --out "${in_run_dir}/scoring.summary.json" --tolerance-sec 2.5 || true
  fi

  log "DONE ${run_id}"
}

post_process() {
  log "running multi-variant ablation across all runs (inside monitor container)"
  docker compose exec -T monitor python3 -m analysis.ablation \
    --avaliacao /opt/troia/avaliacao \
    --out /opt/troia/avaliacao/ablation_results.csv \
    --summary /opt/troia/avaliacao/ablation_summary.csv \
    --window-sec 5 --baseline-sec "$WARMUP_SEC" --tolerance-sec 2.5 || true
  log "aggregating per-run multi-metric summary -> results.csv"
  docker compose exec -T monitor python3 -m analysis.aggregate \
    --avaliacao /opt/troia/avaliacao \
    --out /opt/troia/avaliacao/results.csv || true
}

IFS=',' read -ra CFG_ARR <<< "$CONFIGS"
IFS=',' read -ra SC_ARR <<< "$SCENARIOS"

for config in "${CFG_ARR[@]}"; do
  for scenario in "${SC_ARR[@]}"; do
    for iter in $(seq 1 "$ITERS"); do
      run_one "$config" "$scenario" "$iter"
    done
  done
done

log "matrix complete: ${#CFG_ARR[@]} configs × ${#SC_ARR[@]} scenarios × ${ITERS} iter = $((${#CFG_ARR[@]} * ${#SC_ARR[@]} * ITERS)) runs"

post_process
