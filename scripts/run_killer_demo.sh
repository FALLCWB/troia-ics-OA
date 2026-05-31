#!/usr/bin/env bash
# run_killer_demo.sh — pre-registered killer demo for §V.G of the paper.
#
# Original pre-registration (analysis/killer_demo_preregistration.md, Class 1):
#   H1: P(manifest | provoke_on)  >= 0.95
#   H0: P(manifest | provoke_off) <= 0.20
# Test: two-proportion z-test, two-tailed, alpha = 0.05.
# Sample size: n = 20 per arm (40 total runs, Class 1).
# Powered at >= 0.80 to detect |p1 - p2| >= 0.75.
#
# Amendment 2026-05-19: extended to three trigger classes.
#   Class 1 (data-conditioned):        provoke_off / provoke_on          — original
#   Class 2 (sequence-conditioned):    provoke_seq_off / provoke_seq_on  — new
#   Class 3 (environment-conditioned): provoke_env_off / provoke_env_on  — new
# Total: 6 arms x N_PER_ARM iterations = ~120 runs at default N=20.
#
# Order is fixed per pre-reg convention: control arm before treatment arm
# within each class, classes run in order 1 → 2 → 3.
#
# N_PER_ARM is env-overridable (default 20).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

N_PER_ARM="${N_PER_ARM:-20}"

# --- Class 1: data-conditioned (original pre-registered experiment) ---
echo "[killer-demo] $(date -u +%FT%TZ) Class 1 — control arm (provoke_off, n=${N_PER_ARM})"
./scripts/run_matrix.sh --configs single-plc --scenarios provoke_off --iters "$N_PER_ARM"

echo "[killer-demo] $(date -u +%FT%TZ) Class 1 — treatment arm (provoke_on, n=${N_PER_ARM})"
./scripts/run_matrix.sh --configs single-plc --scenarios provoke_on --iters "$N_PER_ARM"

# --- Class 2: sequence-conditioned ---
echo "[killer-demo] $(date -u +%FT%TZ) Class 2 — control arm (provoke_seq_off, n=${N_PER_ARM})"
./scripts/run_matrix.sh --configs single-plc --scenarios provoke_seq_off --iters "$N_PER_ARM"

echo "[killer-demo] $(date -u +%FT%TZ) Class 2 — treatment arm (provoke_seq_on, n=${N_PER_ARM})"
./scripts/run_matrix.sh --configs single-plc --scenarios provoke_seq_on --iters "$N_PER_ARM"

# --- Class 3: environment-conditioned ---
echo "[killer-demo] $(date -u +%FT%TZ) Class 3 — control arm (provoke_env_off, n=${N_PER_ARM})"
./scripts/run_matrix.sh --configs single-plc --scenarios provoke_env_off --iters "$N_PER_ARM"

echo "[killer-demo] $(date -u +%FT%TZ) Class 3 — treatment arm (provoke_env_on, n=${N_PER_ARM})"
./scripts/run_matrix.sh --configs single-plc --scenarios provoke_env_on --iters "$N_PER_ARM"

# --- Per-class statistical analysis ---
echo "[killer-demo] $(date -u +%FT%TZ) running per-class statistical analysis"
docker compose exec -T monitor python3 -m analysis.killer_demo_analysis \
  --avaliacao /opt/troia/avaliacao \
  --out /opt/troia/avaliacao/killer_demo_summary.json

echo "[killer-demo] done. Summary at avaliacao/killer_demo_summary.json"
