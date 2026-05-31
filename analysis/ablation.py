"""Ablation pipeline — re-fit several detector variants over the same per-run
windowed features and score each variant against the ground-truth events.

This addresses the reviewer concern that the paper's central claim
("multi-channel observability beats latency alone") is unfalsifiable without
single-channel and alternative-model baselines. The detector is stateless and
the windowing is deterministic, so the same `integrity.jsonl`, `availability.jsonl`,
`capture.pcap`, and `chaos_events.jsonl` can be replayed through every variant
without re-running the experimental matrix.

Variants:
    iforest_multi      — Isolation Forest on all four channels (default detector)
    iforest_latency    — Isolation Forest on latency_stddev_ms only
    iforest_availability — Isolation Forest on availability_pct only
    iforest_integrity  — Isolation Forest on integrity_dev_rms only
    iforest_malformed  — Isolation Forest on malformed_rate only
    lof_multi          — Local Outlier Factor (novelty=True) on all four channels

Usage:
    python -m analysis.ablation \\
        --avaliacao avaliacao/ \\
        --out avaliacao/ablation_results.csv \\
        --window-sec 5 --baseline-sec 60 --tolerance-sec 2.5
"""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler

from monitor.detector import FEATURE_NAMES, build_windows, detect, write_jsonl
from metrics.scoring import score


@dataclass(slots=True)
class VariantSpec:
    name: str
    feature_indices: tuple[int, ...] | None
    model: str  # "iforest" or "lof"


VARIANTS: tuple[VariantSpec, ...] = (
    VariantSpec("iforest_multi",        None,    "iforest"),
    VariantSpec("iforest_latency",      (0,),    "iforest"),
    VariantSpec("iforest_availability", (1,),    "iforest"),
    VariantSpec("iforest_integrity",    (2,),    "iforest"),
    VariantSpec("iforest_malformed",    (3,),    "iforest"),
    VariantSpec("lof_multi",            None,    "lof"),
)


COLUMNS = [
    "run_id", "config", "scenario", "iter", "variant",
    "tp", "fp", "fn", "tn", "precision", "recall", "f1", "fpr",
    "n_windows", "n_flagged", "n_events",
]


import re

RUN_ID_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})-(?P<config>[a-z0-9-]+)-(?P<scenario>[a-z0-9-]+)-i(?P<iter>\d+)$"
)


def _detect_with_lof(windows, baseline_sec, random_state=42, feature_indices=None):
    """Same interface as monitor.detector.detect but uses LOF."""
    if not windows:
        return []
    baseline_end_ns = windows[0].window_start_ns + int(baseline_sec * 1_000_000_000)
    baseline = [w for w in windows if w.window_end_ns <= baseline_end_ns]
    if len(baseline) < 3:
        for w in windows:
            w.score = 0.0
            w.is_anomaly = False
        return windows

    def _select(feats):
        return list(feats) if feature_indices is None else [feats[i] for i in feature_indices]

    X_base = np.array([_select(w.features) for w in baseline], dtype=float)
    X_all = np.array([_select(w.features) for w in windows], dtype=float)

    scaler = StandardScaler()
    X_base_scaled = scaler.fit_transform(X_base)
    X_all_scaled = scaler.transform(X_all)

    n_neighbors = min(20, max(1, len(baseline) - 1))
    model = LocalOutlierFactor(n_neighbors=n_neighbors, novelty=True)
    model.fit(X_base_scaled)

    scores = model.decision_function(X_all_scaled)
    preds = model.predict(X_all_scaled)
    for w, s, p in zip(windows, scores, preds, strict=True):
        w.score = float(s)
        w.is_anomaly = bool(p == -1) and w.window_end_ns > baseline_end_ns
    return windows


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
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(obj)
    return out


def run_variant(
    windows,
    variant: VariantSpec,
    baseline_sec: float,
    events: list[dict],
    tolerance_sec: float,
):
    """Re-fit a detector variant on the windows, score against events, return row."""
    # Deep copy of windows so each variant has a fresh state.
    import copy
    w_copy = copy.deepcopy(windows)
    if variant.model == "iforest":
        scored = detect(w_copy, baseline_sec=baseline_sec, feature_indices=variant.feature_indices)
    elif variant.model == "lof":
        scored = _detect_with_lof(w_copy, baseline_sec=baseline_sec, feature_indices=variant.feature_indices)
    else:
        raise ValueError(f"unknown model {variant.model}")

    n_flagged = sum(1 for w in scored if w.is_anomaly)

    # Convert window flags to anomaly dicts shaped for metrics.scoring.
    anomalies = [
        {
            "timestamp_ns": (w.window_start_ns + w.window_end_ns) // 2,
            "is_anomaly": w.is_anomaly,
            "score": w.score,
        }
        for w in scored
    ]
    cm = score(events, anomalies, tolerance_sec=tolerance_sec)
    return {
        "tp": cm.tp, "fp": cm.fp, "fn": cm.fn, "tn": cm.tn,
        "precision": round(cm.precision, 4),
        "recall": round(cm.recall, 4),
        "f1": round(cm.f1, 4),
        "fpr": round(cm.fpr, 4),
        "n_windows": len(scored),
        "n_flagged": n_flagged,
        "n_events": len(events),
    }


def collect(
    avaliacao_dir: Path,
    out_csv: Path,
    window_sec: float,
    baseline_sec: float,
    tolerance_sec: float,
) -> int:
    rows: list[dict] = []
    for entry in sorted(avaliacao_dir.iterdir()):
        if not entry.is_dir():
            continue
        m = RUN_ID_RE.match(entry.name)
        if not m:
            continue
        windows = build_windows(entry, window_sec=window_sec)
        if not windows:
            continue
        events = _load_events(entry / "chaos_events.jsonl")
        for variant in VARIANTS:
            metrics = run_variant(windows, variant, baseline_sec, events, tolerance_sec)
            rows.append({
                "run_id": entry.name,
                "config": m.group("config"),
                "scenario": m.group("scenario"),
                "iter": int(m.group("iter")),
                "variant": variant.name,
                **metrics,
            })

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"wrote {len(rows)} variant-rows -> {out_csv}")
    return len(rows)


def summarise(ablation_csv: Path, summary_csv: Path) -> None:
    """Aggregate by (scenario, variant): mean and std of f1/precision/recall."""
    from collections import defaultdict
    buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"f1": [], "precision": [], "recall": [], "fpr": []}
    )
    with ablation_csv.open() as fh:
        for row in csv.DictReader(fh):
            key = (row["scenario"], row["variant"])
            for m in ("f1", "precision", "recall", "fpr"):
                try:
                    buckets[key][m].append(float(row[m]))
                except (TypeError, ValueError):
                    pass

    out_cols = ["scenario", "variant", "n",
                "f1_mean", "f1_std", "precision_mean", "recall_mean", "fpr_mean"]
    with summary_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_cols)
        writer.writeheader()
        for (scenario, variant), m in sorted(buckets.items()):
            f1 = np.array(m["f1"]) if m["f1"] else np.array([0.0])
            writer.writerow({
                "scenario": scenario,
                "variant": variant,
                "n": len(m["f1"]),
                "f1_mean": round(float(np.mean(f1)), 4),
                "f1_std": round(float(np.std(f1)), 4),
                "precision_mean": round(float(np.mean(m["precision"])) if m["precision"] else 0.0, 4),
                "recall_mean":    round(float(np.mean(m["recall"]))    if m["recall"]    else 0.0, 4),
                "fpr_mean":       round(float(np.mean(m["fpr"]))       if m["fpr"]       else 0.0, 4),
            })
    print(f"wrote summary -> {summary_csv}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out", type=Path, default=Path("avaliacao/ablation_results.csv"))
    p.add_argument("--summary", type=Path, default=Path("avaliacao/ablation_summary.csv"))
    p.add_argument("--window-sec", type=float, default=5.0)
    p.add_argument("--baseline-sec", type=float, default=60.0)
    p.add_argument("--tolerance-sec", type=float, default=2.5)
    args = p.parse_args()
    collect(args.avaliacao, args.out, args.window_sec, args.baseline_sec, args.tolerance_sec)
    summarise(args.out, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
