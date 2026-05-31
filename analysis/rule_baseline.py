"""Rule-based threshold detector as a simple baseline.

The Isolation Forest detector in `monitor/detector.py` is an ML model; the
question Reviewer C raised is whether such a model is actually justified
compared to a simple statistical-threshold rule. This script implements
the latter:

  1. For each run, compute mean and standard deviation of each of the four
     features over its baseline window (first 60 s).
  2. For each subsequent window, flag the window as anomaly if ANY feature
     exceeds its baseline mean by more than k*std (default k = 2.0). The
     check is one-sided (deviations above baseline) for latency_stddev_ms,
     integrity_dev_rms, and malformed_rate; two-sided for availability_pct
     (drops or spikes).
  3. Pair flags against chaos_events with the same 2.5 s tolerance.
  4. Aggregate F1 mean and standard deviation across the ten iterations
     of each scenario.

If a four-line rule matches or beats Isolation Forest, the added ML
complexity is unjustified; if Isolation Forest beats this baseline by a
meaningful margin (say, F1 +0.10) the ML approach is empirically defended.

Output:
  - rule_baseline_summary.csv — one row per scenario: F1 mean and std
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

import numpy as np

from monitor.detector import FEATURE_NAMES, build_windows
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


def score_run_with_rules(run_dir: Path, window_sec: float, baseline_sec: float,
                         k_sigma: float, tolerance_sec: float) -> dict:
    """Score a single run with a threshold rule, return confusion-matrix dict."""
    windows = build_windows(run_dir, window_sec=window_sec)
    if not windows:
        return {"tp": 0, "fp": 0, "fn": 0, "tn": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0, "fpr": 0.0}

    run_start = windows[0].window_start_ns
    baseline_end_ns = run_start + int(baseline_sec * 1_000_000_000)
    baseline = [w.features for w in windows if w.window_end_ns <= baseline_end_ns]
    if not baseline:
        return {"tp": 0, "fp": 0, "fn": 0, "tn": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0, "fpr": 0.0}

    base_arr = np.array(baseline, dtype=float)
    base_mean = base_arr.mean(axis=0)
    base_std = base_arr.std(axis=0)
    # Guard against zero-std features (constant during baseline): use a small
    # epsilon so we don't trip on machine-noise differences.
    base_std = np.maximum(base_std, 1e-6)

    upper = base_mean + k_sigma * base_std
    # availability_pct is the only feature where "below baseline" is also bad.
    lower_avail = base_mean[1] - k_sigma * base_std[1]

    anomalies: list[dict] = []
    for w in windows:
        if w.window_end_ns <= baseline_end_ns:
            # Baseline windows are never flagged.
            anomalies.append({
                "timestamp_ns": (w.window_start_ns + w.window_end_ns) // 2,
                "is_anomaly": False,
                "score": 0.0,
            })
            continue
        f = np.array(w.features, dtype=float)
        # latency / integrity / malformed: above-baseline only.
        upper_breach = (f[0] > upper[0]) or (f[2] > upper[2]) or (f[3] > upper[3])
        # availability: below baseline (drops indicate trouble).
        avail_breach = f[1] < lower_avail
        is_anom = bool(upper_breach or avail_breach)
        anomalies.append({
            "timestamp_ns": (w.window_start_ns + w.window_end_ns) // 2,
            "is_anomaly": is_anom,
            "score": 0.0,
        })

    events = _load_events(run_dir / "chaos_events.jsonl")
    cm = score_cm(events, anomalies, tolerance_sec=tolerance_sec)
    return {"tp": cm.tp, "fp": cm.fp, "fn": cm.fn, "tn": cm.tn,
            "precision": round(cm.precision, 4),
            "recall": round(cm.recall, 4),
            "f1": round(cm.f1, 4),
            "fpr": round(cm.fpr, 4)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out", type=Path, default=Path("avaliacao/rule_baseline_summary.csv"))
    p.add_argument("--k-sigma", type=float, default=2.0,
                   help="threshold in standard deviations above per-run baseline (default 2.0)")
    p.add_argument("--window-sec", type=float, default=5.0)
    p.add_argument("--baseline-sec", type=float, default=60.0)
    p.add_argument("--tolerance-sec", type=float, default=2.5)
    args = p.parse_args()

    by_scen: dict[str, list[dict]] = defaultdict(list)
    for entry in sorted(args.avaliacao.iterdir()):
        if not entry.is_dir():
            continue
        m = RUN_ID_RE.match(entry.name)
        if not m:
            continue
        metrics = score_run_with_rules(
            entry,
            window_sec=args.window_sec,
            baseline_sec=args.baseline_sec,
            k_sigma=args.k_sigma,
            tolerance_sec=args.tolerance_sec,
        )
        by_scen[m.group("scenario")].append(metrics)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["scenario", "n", "f1_mean", "f1_std",
            "precision_mean", "recall_mean", "fpr_mean"]
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for scenario, runs in sorted(by_scen.items()):
            f1s = [r["f1"] for r in runs]
            writer.writerow({
                "scenario": scenario, "n": len(runs),
                "f1_mean": round(float(np.mean(f1s)), 4),
                "f1_std": round(float(np.std(f1s)), 4),
                "precision_mean": round(float(np.mean([r["precision"] for r in runs])), 4),
                "recall_mean": round(float(np.mean([r["recall"] for r in runs])), 4),
                "fpr_mean": round(float(np.mean([r["fpr"] for r in runs])), 4),
            })
    print(f"wrote rule-baseline summary (k={args.k_sigma}sigma) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
