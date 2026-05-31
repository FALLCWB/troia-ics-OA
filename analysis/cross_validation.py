"""Held-out cross-validation for the multi-channel Isolation Forest detector.

The per-run baseline used in `analysis/ablation.py` fits the detector on
the first 60 s of each run and scores the rest of that same run. Reviewer C
correctly objected that this risks train/test contamination and offers no
evidence that the detector generalises across runs. This script implements
a 7-train/3-test split per scenario, repeated K times, and reports F1 mean
and standard deviation per scenario.

Procedure for each (scenario, split-seed):
  1. Random-shuffle the 10 iterations of the scenario.
  2. Take the first 7 as training; concatenate all of their *baseline windows*
     (those entirely within the first 60 s of each run) into a single
     training set.
  3. Fit Isolation Forest (with StandardScaler) on the merged baseline set.
  4. Score every window of each of the 3 held-out runs.
  5. Score TP/FN/FP/TN against each held-out run's chaos_events.
  6. Aggregate F1 across the 3 test runs.
  7. Report mean ± std across the K splits.

Outputs:
  - cv_results.csv      — one row per (scenario, split_seed): F1, precision, recall, FPR
  - cv_summary.csv      — one row per scenario: F1_mean, F1_std (across K splits)
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

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


def collect_runs_by_scenario(avaliacao_dir: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = defaultdict(list)
    for entry in sorted(avaliacao_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = RUN_ID_RE.match(entry.name)
        if not m:
            continue
        out[m.group("scenario")].append(entry)
    return out


def run_one_split(
    train_dirs: list[Path],
    test_dirs: list[Path],
    window_sec: float,
    baseline_sec: float,
    random_state: int,
    tolerance_sec: float,
) -> dict[str, float]:
    """Train IsoForest on the concatenated baseline windows of train_dirs,
    then score every window of every test_dir against its own chaos_events."""
    # Build training set: baseline windows of every train run.
    X_train: list[list[float]] = []
    for d in train_dirs:
        windows = build_windows(d, window_sec=window_sec)
        if not windows:
            continue
        run_start = windows[0].window_start_ns
        baseline_end_ns = run_start + int(baseline_sec * 1_000_000_000)
        for w in windows:
            if w.window_end_ns <= baseline_end_ns:
                X_train.append(w.features)
    if len(X_train) < 5:
        return {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "fpr": 0.0}

    scaler = StandardScaler()
    X_train_arr = np.array(X_train, dtype=float)
    X_train_scaled = scaler.fit_transform(X_train_arr)
    model = IsolationForest(n_estimators=100, contamination="auto", random_state=random_state)
    model.fit(X_train_scaled)

    # Score every test run: aggregate TP/FN/FP/TN.
    tp = fp = fn = tn = 0
    for d in test_dirs:
        windows = build_windows(d, window_sec=window_sec)
        if not windows:
            continue
        run_start = windows[0].window_start_ns
        baseline_end_ns = run_start + int(baseline_sec * 1_000_000_000)
        X = np.array([w.features for w in windows], dtype=float)
        X_scaled = scaler.transform(X)
        preds = model.predict(X_scaled)
        anomalies = []
        for w, p in zip(windows, preds, strict=True):
            is_anom = bool(p == -1) and w.window_end_ns > baseline_end_ns
            anomalies.append({
                "timestamp_ns": (w.window_start_ns + w.window_end_ns) // 2,
                "is_anomaly": is_anom,
                "score": 0.0,
            })
        events = _load_events(d / "chaos_events.jsonl")
        cm = score_cm(events, anomalies, tolerance_sec=tolerance_sec)
        tp += cm.tp; fp += cm.fp; fn += cm.fn; tn += cm.tn

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4), "fpr": round(fpr, 4)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out", type=Path, default=Path("avaliacao/cv_results.csv"))
    p.add_argument("--summary", type=Path, default=Path("avaliacao/cv_summary.csv"))
    p.add_argument("--n-splits", type=int, default=5)
    p.add_argument("--n-train", type=int, default=7)
    p.add_argument("--window-sec", type=float, default=5.0)
    p.add_argument("--baseline-sec", type=float, default=60.0)
    p.add_argument("--tolerance-sec", type=float, default=2.5)
    args = p.parse_args()

    by_scen = collect_runs_by_scenario(args.avaliacao)
    rows: list[dict] = []
    for scenario, runs in by_scen.items():
        if len(runs) < args.n_train + 1:
            continue
        for split in range(args.n_splits):
            rng = random.Random(split)
            shuffled = list(runs)
            rng.shuffle(shuffled)
            train = shuffled[: args.n_train]
            test = shuffled[args.n_train :]
            metrics = run_one_split(train, test, args.window_sec, args.baseline_sec, split, args.tolerance_sec)
            rows.append({"scenario": scenario, "split": split, "n_train": len(train), "n_test": len(test), **metrics})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["scenario", "split", "n_train", "n_test", "tp", "fp", "fn", "tn",
            "precision", "recall", "f1", "fpr"]
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"wrote {len(rows)} CV rows -> {args.out}")

    # Summary by scenario.
    by_scen_summary: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_scen_summary[r["scenario"]].append(r)
    with args.summary.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["scenario", "n_splits",
            "f1_mean", "f1_std", "precision_mean", "recall_mean", "fpr_mean"])
        writer.writeheader()
        for scenario, rs in sorted(by_scen_summary.items()):
            f1s = [r["f1"] for r in rs]
            writer.writerow({
                "scenario": scenario, "n_splits": len(rs),
                "f1_mean": round(float(np.mean(f1s)), 4),
                "f1_std": round(float(np.std(f1s)), 4),
                "precision_mean": round(float(np.mean([r["precision"] for r in rs])), 4),
                "recall_mean": round(float(np.mean([r["recall"] for r in rs])), 4),
                "fpr_mean": round(float(np.mean([r["fpr"] for r in rs])), 4),
            })
    print(f"wrote CV summary -> {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
