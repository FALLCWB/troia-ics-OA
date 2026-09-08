"""Seed-sensitivity analysis for the multi-channel Isolation Forest detector.

The `monitor/detector.py` script hardcodes `random_state=42`. Reviewer C
asked whether the reported F1 numbers are robust to that choice. This
script re-fits the detector N times per run (one fit per seed value)
and reports mean ± std of F1 across seeds per scenario.

Procedure: re-implement the per-run-baseline fit-and-score loop from
`monitor/detector.py`, but iterate over a list of random_state values
instead of a single one. Aggregate per-scenario.

Output:
  - seed_sensitivity_summary.csv — one row per scenario: F1 mean and std
    across seeds.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from monitor.detector import build_windows
from metrics.scoring import score as score_cm


RUN_ID_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})-(?P<config>[a-z0-9-]+)-(?P<scenario>[a-z0-9-]+)-i(?P<iter>\d+)$"
)


def _load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def score_run_with_seed(run_dir: Path, seed: int, window_sec: float,
                        baseline_sec: float, tolerance_sec: float) -> float | None:
    windows = build_windows(run_dir, window_sec=window_sec)
    if not windows:
        # Aborted run: no collector output. Skipped rather than scored as 0.0,
        # which would enter the per-scenario mean as a spurious missed detection.
        return None
    run_start = windows[0].window_start_ns
    baseline_end_ns = run_start + int(baseline_sec * 1_000_000_000)
    baseline = [w.features for w in windows if w.window_end_ns <= baseline_end_ns]
    if len(baseline) < 3:
        return None

    scaler = StandardScaler()
    X_base = scaler.fit_transform(np.array(baseline, dtype=float))
    X_all = scaler.transform(np.array([w.features for w in windows], dtype=float))
    model = IsolationForest(n_estimators=100, contamination="auto", random_state=seed)
    model.fit(X_base)
    preds = model.predict(X_all)
    anomalies = []
    for w, p in zip(windows, preds, strict=True):
        is_anom = bool(p == -1) and w.window_end_ns > baseline_end_ns
        anomalies.append({
            "timestamp_ns": (w.window_start_ns + w.window_end_ns) // 2,
            "is_anomaly": is_anom,
            "score": 0.0,
        })
    events = _load_events(run_dir / "chaos_events.jsonl")
    cm = score_cm(events, anomalies, tolerance_sec=tolerance_sec)
    return cm.f1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out", type=Path, default=Path("avaliacao/seed_sensitivity_summary.csv"))
    p.add_argument("--seeds", nargs="+", type=int, default=[1, 7, 42, 123, 256, 512, 1024, 2048, 4096, 8192])
    p.add_argument("--window-sec", type=float, default=5.0)
    p.add_argument("--baseline-sec", type=float, default=60.0)
    p.add_argument("--tolerance-sec", type=float, default=2.5)
    args = p.parse_args()

    by_scen: dict[str, list[float]] = defaultdict(list)
    for entry in sorted(args.avaliacao.iterdir()):
        if not entry.is_dir():
            continue
        m = RUN_ID_RE.match(entry.name)
        if not m:
            continue
        # Per run, average F1 across seeds.
        f1s = [score_run_with_seed(entry, s, args.window_sec, args.baseline_sec, args.tolerance_sec)
               for s in args.seeds]
        f1s = [f for f in f1s if f is not None]
        if not f1s:
            continue
        by_scen[m.group("scenario")].extend(f1s)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["scenario", "n_observations", "n_seeds",
                                                "f1_mean_over_seeds", "f1_std_over_seeds"])
        writer.writeheader()
        for scenario, f1s in sorted(by_scen.items()):
            writer.writerow({
                "scenario": scenario,
                "n_observations": len(f1s),
                "n_seeds": len(args.seeds),
                "f1_mean_over_seeds": round(float(np.mean(f1s)), 4),
                "f1_std_over_seeds": round(float(np.std(f1s)), 4),
            })
    print(f"wrote seed-sensitivity summary -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
