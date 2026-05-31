#!/usr/bin/env bash
# Wrapper to run only the matrix cells that still need running, in order:
#   1. single-plc / replay        — 10 iter (replay was buggy in v1)
#   2. dual-plc / all 4 scenarios — 40 iter
#   3. redundant-hmi / all 4      — 40 iter
# Total: 90 runs, ~6h45min wall.
#
# Each phase is launched sequentially; the previous one must finish before
# the next begins. Failures in one phase do not block the others.

set -u  # no -e; we want the wrapper to continue across phase failures
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[remaining] %s\n' "$*"; }

log "Phase 1/3 — single-plc / replay (10 iter, ~45min)"
bash scripts/run_matrix.sh --configs single-plc --scenarios replay --iters 10 || log "phase 1 had errors"

log "Phase 2/3 — dual-plc / all scenarios (40 iter, ~3h)"
bash scripts/run_matrix.sh --configs dual-plc --iters 10 || log "phase 2 had errors"

log "Phase 3/3 — redundant-hmi / all scenarios (40 iter, ~3h)"
bash scripts/run_matrix.sh --configs redundant-hmi --iters 10 || log "phase 3 had errors"

log "all phases complete"
