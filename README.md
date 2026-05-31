# troia-ics

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20469770.svg)](https://doi.org/10.5281/zenodo.20469770)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Data License: CC BY 4.0](https://img.shields.io/badge/Data-CC_BY_4.0-orange.svg)](https://creativecommons.org/licenses/by/4.0/)

A reproducible, container-based ICS testbed for **pre-deployment provocation** of dormant supply-chain triggers, with multi-channel observability and Isolation-Forest-based anomaly detection. Backing repository for the IEEE Access manuscript *"Pre-Deployment Provocation of Dormant Supply-Chain Triggers in Industrial Control Systems: A Reproducible Container Framework with Multi-Channel Observability."*

## Why this exists

Supply-chain compromise of ICS equipment can remain dormant for days, months, or years inside a device that looks perfectly sound, until a specific operational context — a particular command sequence, stress condition, or protocol pattern — fires the trigger. Once the device is in production, detection is structurally too late. The framework in this repository reproduces operational context with sufficient fidelity to exercise candidate triggers in isolation, before deployment, and capture the resulting behavioural deviation through multiple independent observability channels. The killer demo covers three trigger classes from the paper's taxonomy: Class 1 (data-conditioned), Class 2 (sequence-conditioned), and Class 3 (environment-conditioned).

The work is especially motivated by jurisdictions that import most of their ICS equipment and operate large, heterogeneous critical infrastructure (energy distribution, oil and gas, water, smart metering) — Brazil being the explicit case in the paper. The framework is open-source, runs on commodity hardware, and is designed to be the methodological scaffolding for a future hardware test rig (in development at the IME LaSC) that will validate the methodology against a real injected supply-chain payload.

## How it maps to the paper's framework

| Paper layer                | Concrete component                                            |
|----------------------------|---------------------------------------------------------------|
| Digital Twin proxy         | In-PLC process model (`tank_pid.st` PID + tank simulation)    |
| Device Under Test          | OpenPLC v3 runtime container                                  |
| HiL boundary               | SCADA-LTS HMI container (real Modbus/TCP master)              |
| Trigger provocation        | `attacker/inject.py` — 3 trigger scenarios (see below)        |
| Observability — channel 1  | Device availability (Docker engine events, restart count)     |
| Observability — channel 2  | Process-variable integrity (Modbus HR polling vs setpoint)    |
| Observability — channel 3  | Communication latency (Modbus query–response interval stddev) |
| Observability — channel 4  | Modbus protocol anomalies (pcap + Scapy parser, per-window)   |
| Fused detector             | Isolation Forest over the four-channel feature vector         |
| Ablation                   | Per-channel + LOF variants over the same recorded run         |

## Trigger scenarios

Nine attack modes in `attacker/inject.py`. The first three are synthetic operational-context proxies for the trigger taxonomy of the paper. The remaining six implement the pre-registered killer demo (§V.G of the paper), where a genuinely hidden contextual trigger embedded in `tank_pid.st` is forced to manifest only under reconstructed context, across three trigger classes.

| Mode               | Trigger class (paper taxonomy)                              | Channels primarily exercised                           |
|--------------------|-------------------------------------------------------------|--------------------------------------------------------|
| `malformed`        | Combinational / data-conditioned                            | Modbus protocol anomalies                              |
| `mutate`           | Environment-conditioned (runtime context shift)             | Availability + integrity                               |
| `replay`           | Sequential / sequence-conditioned                           | Latency + integrity                                    |
| `provoke_on`       | Class 1 data-conditioned (killer demo — treatment)          | All four — manifestation forced                        |
| `provoke_off`      | Class 1 data-conditioned (killer demo — control)            | All four — trigger stays dormant                       |
| `provoke_seq_on`   | Class 2 sequence-conditioned (killer demo — treatment)      | Integrity (Class 2 trigger counter), latency           |
| `provoke_seq_off`  | Class 2 sequence-conditioned (killer demo — control)        | none                                                   |
| `provoke_env_on`   | Class 3 environment-conditioned (killer demo — treatment)   | Availability + integrity (environment threshold)       |
| `provoke_env_off`  | Class 3 environment-conditioned (killer demo — control)     | none                                                   |

Real-trojan validation against a captured supply-chain payload is in development at the IME LaSC and is the principal next step.

## Quick reproduction

The top-level `Makefile` exposes six reproducibility targets ordered by cost:

| Target                               | Time          | Produces                                                    |
|--------------------------------------|---------------|-------------------------------------------------------------|
| `make smoke-test`                    | < 5 min       | Testbed sanity check                                        |
| `make reproduce-verify`              | < 10 min      | Smoke + 1 baseline run                                      |
| `make reproduce-table-2`             | ~ 8 min       | 8 runs reproducing Table II                                 |
| `make reproduce-killer-demo-class1`  | ~ 3 h         | 40 runs — Class 1 only (original pre-reg) + z-test          |
| `make reproduce-killer-demo`         | ~ 9 h         | ~120 runs — Classes 1+2+3 + per-class z-test                |
| `make reproduce-full`                | ~ 10 CPU-h    | All 40 main runs + ablation + CV + 3σ rule                  |

Requirements: Docker Engine 24+, ~15 GB free disk for pcap captures, 2 GB RAM. The killer demo re-runs the pre-registered binary-separation experiment described in `analysis/killer_demo_preregistration.md`.

## Architecture

```
┌────────────────────── troia-net (Docker bridge) ──────────────────────────┐
│                                                                           │
│   ┌──────────┐ Modbus/TCP 502 ┌──────────┐ HTTP 8080 ┌──────────┐         │
│   │   plc    │ ◄────────────► │   hmi    │ ◄───────► │ (browser)│         │
│   │ OpenPLC  │                │ SCADA-LTS│           └──────────┘         │
│   └────┬─────┘                └────┬─────┘                                │
│        │                            │                                     │
│        ▼ pcap                       ▼ JDBC                                │
│   ┌──────────┐                ┌──────────┐                                │
│   │ monitor  │                │ database │                                │
│   │ metrics  │                │ MySQL 5.7│                                │
│   │ detector │                └──────────┘                                │
│   └──────────┘                                                            │
│        ▲                                                                  │
│        │                       ┌──────────┐                               │
│        └────── pcap ──────────►│ attacker │ ──── triggers ─►  plc / hmi   │
│                                │ inject.py│                               │
│                                └──────────┘                               │
└───────────────────────────────────────────────────────────────────────────┘
```

## Stack (100% open source)

| Component                     | License        | Pinned via                            |
|-------------------------------|----------------|---------------------------------------|
| OpenPLC v3                    | GPLv3          | commit hash in `containers/plc/Dockerfile` |
| SCADA-LTS                     | GPLv2          | `scadalts/scadalts:latest` (see VERSIONS.md) |
| MySQL 5.7                     | GPLv2          | sha256 digest in `docker-compose.yml` |
| Docker Engine 24+             | Apache 2.0     | system package                        |
| Ubuntu 24.04 LTS              | Multiple FOSS  | `ubuntu:24.04` tag                    |
| Python 3.12                   | PSF            | venv pinned in `pyproject.toml`       |
| scikit-learn 1.5+             | BSD-3          | `pyproject.toml`                      |
| Scapy 2.5+                    | GPLv2          | per-container `requirements.txt`      |
| pymodbus 3.6+                 | BSD-3          | per-container `requirements.txt`      |

See `VERSIONS.md` for digests and refresh procedure. The repository itself is Apache 2.0 (see `LICENSE`).

## Quickstart

```bash
git clone https://github.com/FALLCWB/troia-ics-OA.git
cd troia-ics-OA
docker compose up -d
./scripts/smoke_test.sh
```

Expected: PLC web UI healthy in <60 s, SCADA-LTS HTTP responding in <240 s, monitor container alive. Modbus :502 only listens after `tank_pid.st` is loaded — `containers/plc/bootstrap.sh` tries to do this automatically; if it fails (OpenPLC UI HTML drift), see `containers/plc/README.md` for the one-time UI step. The program persists in the `plc-data` Docker volume after the first successful load.

## Running the experimental matrix

```bash
# Dry-run: see the 120-run plan without executing.
./scripts/run_matrix.sh --dry-run

# Full matrix: 3 configs × 4 scenarios × 10 iter = 120 runs, ~10 h wall time.
./scripts/run_matrix.sh
```

Each run writes to `avaliacao/<YYYY-MM-DDTHHMMSS>-<config>-<scenario>-i<iter>/` containing per-channel JSONL streams (`availability.jsonl`, `integrity.jsonl`), a pcap, the chaos-event ground truth, and per-run scoring side-cars.

At the end of the matrix, `run_matrix.sh` automatically invokes:
- `analysis/aggregate.py` → `avaliacao/results.csv` (per-run multi-metric summary)
- `analysis/ablation.py` → `avaliacao/ablation_results.csv` + `ablation_summary.csv` (six detector variants over the same recorded runs)

## Ablation: six detector variants on the same data

The detector is stateless, so the same recorded per-channel JSONLs are re-scored by every variant — no need to re-run the matrix.

| Variant                | Model            | Channels        | Purpose                       |
|------------------------|------------------|-----------------|-------------------------------|
| `iforest_multi`        | Isolation Forest | all four        | Default (paper headline)      |
| `iforest_latency`      | Isolation Forest | latency only    | Single-channel baseline       |
| `iforest_availability` | Isolation Forest | availability    | Single-channel baseline       |
| `iforest_integrity`    | Isolation Forest | integrity       | Single-channel baseline       |
| `iforest_malformed`    | Isolation Forest | malformed rate  | Single-channel baseline       |
| `lof_multi`            | LOF (novelty)    | all four        | Alternative-model baseline    |

All variants use the same standardised features (StandardScaler fit on the per-run baseline window) and the same per-run baseline policy.

## Repository layout

```
troia-ics/
├── containers/
│   ├── plc/           # OpenPLC v3 Dockerfile + tank_pid.st + auto-bootstrap
│   ├── hmi/           # SCADA-LTS Dockerfile (uses upstream image)
│   ├── attacker/      # Ubuntu + scapy + pymodbus
│   └── monitor/       # Ubuntu + Python + scikit-learn
├── monitor/           # detector.py (Isolation Forest, multi-channel)
├── metrics/           # availability, integrity, modbus_parser, scoring
├── attacker/          # inject.py (9 trigger modes, 3 killer-demo classes)
├── analysis/          # aggregate.py, plots.py, ablation.py
├── scripts/           # smoke_test.sh, run_matrix.sh
├── compose-overlays/  # single-plc, dual-plc, redundant-hmi
├── avaliacao/         # experiment outputs (gitignored except results.csv)
├── docker-compose.yml
├── pyproject.toml
├── VERSIONS.md
└── LICENSE
```

## Citing

Cite the dataset as:

> F. A. L. Lemos, F. M. Priotto, E. Oroski, R. A. de Faria, and P. C. Pellanda (2026). *troia-ics: Provocation Testing for Dormant Supply-Chain Triggers in Industrial Control Systems — code and dataset* (v1.0.0). Zenodo. https://doi.org/10.5281/zenodo.20469770

BibTeX:

```bibtex
@dataset{lemos2026troiadataset,
  author       = {Lemos, Filipe Augusto da Luz and
                  Priotto, Felipe Messias and
                  Oroski, Elder and
                  de Faria, Rubens Alexandre and
                  Pellanda, Paulo C{\'e}sar},
  title        = {{troia-ics: Provocation Testing for Dormant Supply-Chain
                   Triggers in Industrial Control Systems --- code and dataset}},
  month        = may,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v1.0.0},
  doi          = {10.5281/zenodo.20469770},
  url          = {https://doi.org/10.5281/zenodo.20469770}
}
```

The IEEE Access manuscript is in submission; this section will be updated with the journal citation upon publication.

## License

Code: Apache 2.0 (see `LICENSE`). Data: CC BY 4.0.
