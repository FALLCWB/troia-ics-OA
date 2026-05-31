# Killer Demo — Pre-Registration

**Status:** registered before running any test. This document timestamps the analysis plan so that the result reported in §V.G of the paper cannot be tuned post-hoc.

## Hypothesis

The PLC firmware (`containers/plc/programs/tank_pid.st`) contains a hidden contextual trigger: a dormant supply-chain payload that activates only when the firmware observes simultaneously `HR1032 == 17` and `HR1033 == 42` for five consecutive scan cycles (500 ms). The trigger, once active, increments the `trigger_fired` counter at `HR1034` and saturates the setpoint to 950.

We test the inversion-of-paradigm claim of the paper:

- **Null hypothesis H0:** the trigger fires at the same rate whether or not the testbed reconstructs the activation context.
- **Alternative H1:** the trigger fires materially more often under reconstructed context (the testbed forces the hidden payload to manifest).

Operationally, the manifestation indicator per run is `IntegritySummary.manifested`, defined in `metrics/integrity.py` as `trigger_fired_max > 0` — the `HR1034` counter became non-zero at some point during the run.

Expected effect sizes (chosen before running):

- `P(manifest | provoke_on)  >= 0.95` (the testbed reconstructs the context faithfully; the trigger almost always fires).
- `P(manifest | provoke_off) <= 0.20` (the trigger has no reason to fire; any non-zero rate is residual noise, e.g., container restarts).

## Test

- **Statistical test:** two-proportion z-test, two-tailed.
- **α (significance):** 0.05.
- **Bonferroni correction:** none — this is a single confirmatory test of the central claim. Other multi-scenario hypotheses in the paper are corrected separately in §V.F.
- **Effect size to detect:** $|p_1 - p_2| \geq 0.75$.
- **Power target:** 0.80.
- **Sample size implied:** n = 19 per arm at α = 0.05, two-tailed (via `power.prop.test`). We round up to **n = 20 per arm** (40 runs total) for headroom.

## Experimental design

- **Arms:** `provoke_off` (control, n = 20) and `provoke_on` (treatment, n = 20).
- **Ordering:** control arm runs first, then treatment arm. No peeking at p-value between or within arms. This is enforced by the shell pipeline of `scripts/run_killer_demo.sh`, which calls the analysis script only after both arms complete.
- **Per-run structure:** identical to the rest of the matrix (20 s pre-settle, 60 s warm-up, 180 s active, 30 s cool-down). Each run produces a directory under `avaliacao/` with `integrity.summary.json` carrying the `manifested` field.
- **Randomisation:** the PLC program is identical across runs; the only manipulated variable is whether the attacker writes the activation context (17 to HR1032 and 42 to HR1033) or not.

## Pre-specified analysis pipeline

`analysis/killer_demo_analysis.py` will:

1. Glob `avaliacao/*-single-plc-provoke_off-i*/integrity.summary.json` and `avaliacao/*-single-plc-provoke_on-i*/integrity.summary.json`.
2. For each arm, count runs with `manifested == true`.
3. Compute the two-proportion z-statistic and two-tailed p-value.
4. Compute 95% Wilson confidence intervals for each proportion.
5. Compute median manifestation latency (seconds from run start to `trigger_fired_first_ts_ns`) for the provoke_on arm.
6. Emit `avaliacao/killer_demo_summary.json` with all the above.

## Stop conditions / data exclusion

- A run is **excluded** only if it failed to record `integrity.summary.json` (infrastructure failure, not data anomaly). All other runs, regardless of outcome, are kept.
- No early stopping. The test is run exactly once on the full n = 20/20 dataset.

## Reporting commitments

- We will report `p_on`, `p_off`, the two-proportion z-statistic, the two-tailed p-value, and the 95% CIs on the two proportions.
- We will report the result whether or not it supports H1.
- The output of `analysis/killer_demo_analysis.py` is the source of truth; any number quoted in the paper that does not match the JSON is a typographical error and the JSON wins.

## Versioning

- Paper repo commit at registration: see git log of `troia-ics-paper` repo.
- Code repo commit at registration: see git log of `troia-ics` repo.
- PLC program file: `containers/plc/programs/tank_pid.st` with hidden trigger lines marked by the `Hidden contextual trigger` comment block.

---

## Amendment (2026-05-19)

**Filed:** 2026-05-19 (during the LXC 313 overnight batch on Proxmox), after smoke testing and before the main 15-iteration-per-arm batch was run.

### Scope change

The original pre-registration above scoped a single confirmatory two-proportion z-test for **Class 1 (data-conditioned)** only, comparing `provoke_off` vs `provoke_on`, n = 20 per arm.

The experiment has since been extended to run three trigger classes in parallel: Class 1 (data-conditioned), Class 2 (sequence-conditioned), and Class 3 (environment-conditioned). This amendment documents the change so that no post-hoc reclassification can occur.

### Classes 2 and 3 are exploratory

Classes 2 (sequence-conditioned, `provoke_seq_on` vs `provoke_seq_off`) and 3 (environment-conditioned, `provoke_env_on` vs `provoke_env_off`) are amended here as **exploratory replications** of the same methodology primitive across additional trigger classes from the §III.A taxonomy. Their p-values are descriptive, not confirmatory, and we do not apply familywise correction beyond Class 1.

Class 1 retains its confirmatory status and α = 0.05 threshold as pre-registered. The paper will report Class 1 as confirmatory and Classes 2–3 as exploratory replications, per this amendment (see §V.G).

### Manifestation key per class

| Class | Description | Manifestation indicator |
|---|---|---|
| 1 | Data-conditioned | `trigger_fired_max > 0` |
| 2 | Sequence-conditioned | `trigger_seq_fired_max > 0` |
| 3 | Environment-conditioned | `trigger_env_fired_max > 0` |

### Cross-class cascade (known and intentional)

Smoke testing on 2026-05-19 confirmed that the Class 2 payload (which forces the process setpoint to 50) drives the process variable into the Class 3 trigger envelope, causing Class 3 to also fire on every Class 2 treatment run. This is a **known and intentional** consequence of the cross-class payload couplings encoded in `tank_pid.st`, not a measurement artefact.

The per-class analyses remain uncontaminated: the Class 2 test compares Class 2 counters (`trigger_seq_fired_max`) in `provoke_seq_on` vs `provoke_seq_off`; the Class 3 statistics use only `provoke_env_on` vs `provoke_env_off` runs. The cascade therefore does not inflate or deflate any per-class proportion estimate.

### Ordering and stopping

Same policy as original: control arm runs first, treatment arm second, per class. No peeking at p-values between or within arms. No early stopping.
