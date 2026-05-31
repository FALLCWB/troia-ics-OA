# troia-ics — reproducibility targets.
#
# Quick reproduction of the headline tables and figures of the paper, ordered
# from the cheapest sanity check (smoke-test, < 5 min) to full reproduction
# (reproduce-full, ~10 CPU-hours). Each target is self-contained: bring the
# testbed up, run the relevant subset, tear down. Re-running a target is
# idempotent — finished runs go to avaliacao/ with a UTC timestamp.
#
# Prerequisites: Docker Engine 24+ (or Podman 4+ via DOCKER_HOST), make,
# git, ~15 GB free disk for the full matrix.

SHELL := /bin/bash

.PHONY: help smoke-test reproduce-verify reproduce-table-2 reproduce-killer-demo reproduce-killer-demo-class1 reproduce-full clean

help:
	@echo "troia-ics make targets:"
	@echo ""
	@echo "  make smoke-test                  quick infra check (~5 min)"
	@echo "  make reproduce-verify            smoke + 1 baseline run to check detector"
	@echo "  make reproduce-table-2           8 runs subset producing Table II (~8 min)"
	@echo "  make reproduce-killer-demo       ~120 runs — Classes 1+2+3 killer demo (~9 h)"
	@echo "  make reproduce-killer-demo-class1 40 runs — Class 1 only (original pre-reg, ~3 h)"
	@echo "  make reproduce-full              all 40 main runs + analyses (~10 CPU-h)"
	@echo "  make clean                       docker compose down + remove avaliacao/"

smoke-test:
	@./scripts/smoke_test.sh

reproduce-verify:
	@./scripts/smoke_test.sh
	@./scripts/run_matrix.sh --configs single-plc --scenarios baseline --iters 1
	@echo "verification OK — see avaliacao/ for the produced run"

reproduce-table-2:
	@echo "Running subset matrix (1 config x 4 scenarios x 2 iters = 8 runs, ~8 min)..."
	@./scripts/run_matrix.sh --configs single-plc \
	    --scenarios baseline,malformed,mutate,replay --iters 2
	@echo "Done. Aggregated metrics in avaliacao/results.csv"

# reproduce-killer-demo: runs all three trigger classes (Class 1 data-conditioned,
# Class 2 sequence-conditioned, Class 3 environment-conditioned).
# 40 main runs (Class 1) + 40 each for Classes 2 and 3 = ~120 runs total (~9 h wall time).
# Amendment 2026-05-19 extends the original Class-1-only pre-registration.
reproduce-killer-demo:
	@./scripts/run_killer_demo.sh

# reproduce-killer-demo-class1: reproduces ONLY the original pre-registered
# Class 1 confirmatory experiment (provoke_off vs provoke_on, n=20 per arm,
# 40 runs total, ~3 h). Use this to replicate §V.G as it stood at submission.
reproduce-killer-demo-class1:
	@N_PER_ARM="${N_PER_ARM:-20}"; \
	echo "[killer-demo-class1] $$(date -u +%FT%TZ) control arm (provoke_off, n=$$N_PER_ARM)"; \
	./scripts/run_matrix.sh --configs single-plc --scenarios provoke_off --iters "$$N_PER_ARM"; \
	echo "[killer-demo-class1] $$(date -u +%FT%TZ) treatment arm (provoke_on, n=$$N_PER_ARM)"; \
	./scripts/run_matrix.sh --configs single-plc --scenarios provoke_on --iters "$$N_PER_ARM"; \
	echo "[killer-demo-class1] $$(date -u +%FT%TZ) running statistical analysis"; \
	docker compose exec -T monitor python3 -m analysis.killer_demo_analysis \
	  --avaliacao /opt/troia/avaliacao \
	  --out /opt/troia/avaliacao/killer_demo_class1_summary.json; \
	echo "[killer-demo-class1] done. Summary at avaliacao/killer_demo_class1_summary.json"

reproduce-full:
	@echo "Running full 40-run matrix (~10 CPU-hours)..."
	@./scripts/run_matrix.sh --configs single-plc \
	    --scenarios baseline,malformed,mutate,replay --iters 10
	@echo "Aggregating ablation + analyses..."
	@docker compose exec -T monitor python3 -m analysis.cross_validation
	@docker compose exec -T monitor python3 -m analysis.rule_baseline
	@docker compose exec -T monitor python3 -m analysis.rule_baseline --k-sigma 3.0 \
	    --out /opt/troia/avaliacao/rule_baseline_3sigma_summary.csv
	@docker compose exec -T monitor python3 -m analysis.seed_sensitivity
	@echo "All analyses complete. CSV summaries in avaliacao/."

clean:
	@docker compose down -v --remove-orphans
	@echo "Containers down. To remove run artefacts: rm -rf avaliacao/2026-*"
