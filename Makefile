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

.PHONY: help smoke-test reproduce-verify reproduce-table-3-lite \
        reproduce-binary-separation-all reproduce-binary-separation-class1 \
        reproduce-full clean \
        reproduce-table-2 reproduce-killer-demo reproduce-killer-demo-class1

help:
	@echo "troia-ics make targets:"
	@echo ""
	@echo "  make smoke-test                  quick infra check (~5 min)"
	@echo "  make reproduce-verify            smoke + 1 baseline run to check detector"
	@echo "  make reproduce-table-3-lite      8 runs subset producing Table III (~8 min)"
	@echo "  make reproduce-binary-separation-all"
	@echo "                                   100 runs — Classes 1+2+3 at the reported"
	@echo "                                   arm sizes (~8.5 h)"
	@echo "  make reproduce-binary-separation-class1"
	@echo "                                   40 runs — Class 1 only (original pre-reg, ~3 h)"
	@echo "  make reproduce-full              all 40 main runs + analyses (~10 CPU-h)"
	@echo ""
	@echo "  (reproduce-table-2, reproduce-killer-demo and reproduce-killer-demo-class1"
	@echo "   remain as aliases of the three targets above.)"
	@echo "  make clean                       docker compose down + remove avaliacao/"

smoke-test:
	@./scripts/smoke_test.sh

reproduce-verify:
	@./scripts/smoke_test.sh
	@./scripts/run_matrix.sh --configs single-plc --scenarios baseline --iters 1
	@echo "verification OK — see avaliacao/ for the produced run"

reproduce-table-3-lite:
	@echo "Running subset matrix (1 config x 4 scenarios x 2 iters = 8 runs, ~8 min)..."
	@./scripts/run_matrix.sh --configs single-plc \
	    --scenarios baseline,malformed,mutate,replay --iters 2
	@echo "Done. Aggregated metrics in avaliacao/results.csv"

# reproduce-binary-separation-all: runs all three trigger classes (Class 1
# data-conditioned, Class 2 sequence-conditioned, Class 3 environment-conditioned)
# at the arm sizes the article reports: 20 per arm for Class 1 and 15 per arm for
# Classes 2 and 3, i.e. 40 + 30 + 30 = 100 runs (~8.5 h wall time).
# Amendment 2026-05-19 extends the original Class-1-only pre-registration and
# fixes 15 per arm for the two exploratory classes.
#
# The deposited record holds 104 binary-separation runs; the extra four are a
# smoke sweep that ran before the amendment was filed and are excluded from the
# reported analysis. A fresh execution reproduces the registered batch only.
reproduce-binary-separation-all:
	@./scripts/run_killer_demo.sh

# reproduce-binary-separation-class1: reproduces ONLY the original pre-registered
# Class 1 confirmatory experiment (provoke_off vs provoke_on, n=20 per arm,
# 40 runs total, ~3 h). Use this to replicate the confirmatory test as it stood
# at the original pre-registration.
reproduce-binary-separation-class1:
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
	@docker compose exec -T monitor python3 -m analysis.ablation
	@docker compose exec -T monitor python3 -m analysis.roc_sweep
	@echo "All analyses complete. CSV summaries in avaliacao/."

# --- Backwards-compatible aliases for the pre-2026-09 target names. ---
reproduce-table-2: reproduce-table-3-lite
reproduce-killer-demo: reproduce-binary-separation-all
reproduce-killer-demo-class1: reproduce-binary-separation-class1

clean:
	@docker compose down -v --remove-orphans
	@echo "Containers down. To remove run artefacts: rm -rf avaliacao/2026-*"
