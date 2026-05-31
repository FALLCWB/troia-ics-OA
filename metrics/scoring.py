"""Detection-accuracy scoring.

Joins two timestamped streams:
  - chaos_events.jsonl  — ground-truth attack events {ts_ns, scenario, kind}
  - anomalies.jsonl     — LOF detector output      {ts_ns, score, is_anomaly}

Within a tolerance window (default ±2 s), an LOF anomaly flag co-occurring with
a chaos event counts as a true positive; an LOF flag with no event nearby is a
false positive; a chaos event with no LOF flag nearby is a false negative.

Computes precision / recall / F1 / FPR and writes a JSON summary.

Usage:
    python -m metrics.scoring \\
        --events avaliacao/<run>/chaos_events.jsonl \\
        --anomalies avaliacao/<run>/anomalies.jsonl \\
        --out avaliacao/<run>/scoring.summary.json \\
        --tolerance-sec 2.0
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ConfusionMatrix:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def fpr(self) -> float:
        d = self.fp + self.tn
        return self.fp / d if d else 0.0

    def to_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "fpr": round(self.fpr, 4),
        }


def _load_ns(path: Path, ts_key: str) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if ts_key not in obj:
                continue
            out.append(obj)
    out.sort(key=lambda o: o[ts_key])
    return out


def score(
    events: list[dict],
    anomalies: list[dict],
    tolerance_sec: float = 2.0,
    *,
    anomaly_key: str = "is_anomaly",
    events_ts_key: str = "ts_ns",
    anom_ts_key: str = "timestamp_ns",
) -> ConfusionMatrix:
    """Pair ground-truth events with anomaly flags within a tolerance window."""
    tol_ns = int(tolerance_sec * 1_000_000_000)
    cm = ConfusionMatrix()

    # Sort by ts.
    events_sorted = sorted(events, key=lambda o: o[events_ts_key])
    anoms_sorted = sorted(anomalies, key=lambda o: o[anom_ts_key])

    # Greedy nearest-neighbour matching with consumption.
    used_anom: set[int] = set()
    for ev in events_sorted:
        ev_ts = ev[events_ts_key]
        best_idx = -1
        best_dist = math.inf
        for i, an in enumerate(anoms_sorted):
            if i in used_anom or not an.get(anomaly_key):
                continue
            d = abs(an[anom_ts_key] - ev_ts)
            if d <= tol_ns and d < best_dist:
                best_idx = i
                best_dist = d
        if best_idx >= 0:
            cm.tp += 1
            used_anom.add(best_idx)
        else:
            cm.fn += 1

    # Remaining anomaly flags with no event = FP; non-flagged samples = TN.
    for i, an in enumerate(anoms_sorted):
        if an.get(anomaly_key) and i not in used_anom:
            cm.fp += 1
        elif not an.get(anomaly_key):
            cm.tn += 1

    return cm


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events", required=True, type=Path)
    p.add_argument("--anomalies", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--tolerance-sec", type=float, default=2.0)
    args = p.parse_args()

    events = _load_ns(args.events, "ts_ns")
    anomalies = _load_ns(args.anomalies, "timestamp_ns")
    cm = score(events, anomalies, tolerance_sec=args.tolerance_sec)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cm.to_dict(), indent=2))
    print(json.dumps(cm.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
