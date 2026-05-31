"""Multi-channel anomaly detector for the troia-ics testbed.

Reads per-window samples from the four metric collectors (latency, availability,
integrity, modbus_parser) of a single experimental run, fits an Isolation Forest
on the baseline window, and scores every subsequent window as anomalous or not.

This is the operationalization of the *Observability* layer of the framework:
multi-channel signals fused into a single anomaly score, which the prior paper
reduced to "latency stddev increased 13x" and which we now extend with
device availability, process-variable integrity, and protocol anomaly signals.

Usage (run from the host, after a single experimental run completes):
    python -m monitor.detector \\
        --run-dir avaliacao/<run-id>/ \\
        --window-sec 5 \\
        --baseline-sec 60 \\
        --out avaliacao/<run-id>/anomalies.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler


FEATURE_NAMES = (
    "latency_stddev_ms",       # from integrity.jsonl: stddev of consecutive Modbus read intervals
    "availability_pct",         # from availability.jsonl: container uptime fraction in window
    "integrity_dev_rms",        # from integrity.jsonl: RMS deviation of level from setpoint
    "malformed_rate",           # from modbus.summary.json broken into windows: malformed PDUs / total PDUs
)


@dataclass(slots=True)
class WindowFeature:
    window_start_ns: int
    window_end_ns: int
    features: list[float]
    is_anomaly: bool = False
    score: float = 0.0

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                # Window centre is what we report as timestamp_ns so that
                # metrics.scoring (which expects timestamp_ns) can pair this
                # against chaos_events.jsonl directly.
                "timestamp_ns": (self.window_start_ns + self.window_end_ns) // 2,
                "window_start_ns": self.window_start_ns,
                "window_end_ns": self.window_end_ns,
                "features": dict(zip(FEATURE_NAMES, self.features, strict=True)),
                "score": round(self.score, 6),
                "is_anomaly": self.is_anomaly,
            }
        )


def _per_window_malformed_rate(run_dir: Path, w_start: int, w_end: int) -> float:
    """Return malformed/total ratio for the given window.

    Prefers metrics.modbus_parser per-window output (modbus_windows.jsonl).
    Falls back to modbus.summary.json (aggregate, uniformly distributed across
    all windows of the run) if the per-window file is absent.
    """
    windows_file = run_dir / "modbus_windows.jsonl"
    if windows_file.exists():
        with windows_file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("window_start_ns") == w_start and rec.get("window_end_ns") == w_end:
                    return float(rec.get("malformed_rate", 0.0))
        # Per-window file exists but no record for this exact window -> 0.
        return 0.0

    aggregate = run_dir / "modbus.summary.json"
    if aggregate.exists():
        mb = json.loads(aggregate.read_text())
        total = mb.get("total_pdus", 0)
        malformed = mb.get("malformed", 0)
        return malformed / total if total else 0.0
    return 0.0


def _load_jsonl(path: Path) -> list[dict]:
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


def build_windows(
    run_dir: Path,
    window_sec: float = 5.0,
) -> list[WindowFeature]:
    """Bucket the per-sample JSONLs into fixed-width time windows."""
    integ = _load_jsonl(run_dir / "integrity.jsonl")
    avail = _load_jsonl(run_dir / "availability.jsonl")
    if not integ:
        return []

    # Time bounds from integrity (highest frequency, 10 Hz).
    t_min = min(s["ts_ns"] for s in integ)
    t_max = max(s["ts_ns"] for s in integ)
    window_ns = int(window_sec * 1_000_000_000)
    n_windows = max(1, math.ceil((t_max - t_min) / window_ns))

    windows: list[WindowFeature] = []
    for w in range(n_windows):
        w_start = t_min + w * window_ns
        w_end = w_start + window_ns

        integ_in = [s for s in integ if w_start <= s["ts_ns"] < w_end]
        avail_in = [s for s in avail if w_start <= s["ts_ns"] < w_end]

        # Feature 1: latency stddev — interval between consecutive integrity reads.
        if len(integ_in) >= 2:
            ts = sorted(s["ts_ns"] for s in integ_in)
            intervals_ms = [(ts[i] - ts[i - 1]) / 1_000_000.0 for i in range(1, len(ts))]
            latency_std = float(np.std(intervals_ms))
        else:
            latency_std = 0.0

        # Feature 2: availability percent — running containers / total samples.
        if avail_in:
            running = sum(1 for s in avail_in if s.get("state") == "running")
            availability_pct = 100.0 * running / len(avail_in)
        else:
            availability_pct = 100.0  # no samples = assume up

        # Feature 3: integrity RMS deviation of level from setpoint.
        if integ_in:
            devs = [s["level"] - s["setpoint"] for s in integ_in]
            integrity_dev_rms = float(np.sqrt(np.mean(np.square(devs))))
        else:
            integrity_dev_rms = 0.0

        # Feature 4: malformed_rate. Prefer per-window counts produced by
        # metrics.modbus_parser --windows-out; fall back to the aggregate rate
        # uniformly distributed across the run if the per-window file is absent.
        malformed_rate = _per_window_malformed_rate(run_dir, w_start, w_end)

        windows.append(
            WindowFeature(
                window_start_ns=w_start,
                window_end_ns=w_end,
                features=[latency_std, availability_pct, integrity_dev_rms, malformed_rate],
            )
        )
    return windows


def detect(
    windows: list[WindowFeature],
    baseline_sec: float,
    contamination: float | str = "auto",
    random_state: int = 42,
    feature_indices: tuple[int, ...] | None = None,
) -> list[WindowFeature]:
    """Fit IsolationForest on the first baseline_sec of windows, score the rest.

    feature_indices selects a subset of the FEATURE_NAMES columns; None means
    use all four channels (the multi-channel detector). Single-channel ablations
    pass e.g. (0,) for latency-only.

    The feature matrix is standardised (zero mean, unit variance) on the baseline
    before fitting; this prevents the four heterogeneous channels (ms, percent,
    Modbus units, fraction) from biasing the axis-aligned splits of Isolation
    Forest toward whichever channel happens to have the largest numeric range.
    """
    if not windows:
        return []

    baseline_end_ns = windows[0].window_start_ns + int(baseline_sec * 1_000_000_000)
    baseline = [w for w in windows if w.window_end_ns <= baseline_end_ns]
    if len(baseline) < 3:
        # Not enough baseline; mark everything as not-anomaly with zero score.
        for w in windows:
            w.score = 0.0
            w.is_anomaly = False
        return windows

    def _select(feats: list[float]) -> list[float]:
        return list(feats) if feature_indices is None else [feats[i] for i in feature_indices]

    X_base = np.array([_select(w.features) for w in baseline], dtype=float)
    X_all = np.array([_select(w.features) for w in windows], dtype=float)

    scaler = StandardScaler()
    X_base_scaled = scaler.fit_transform(X_base)
    X_all_scaled = scaler.transform(X_all)

    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=random_state,
    )
    model.fit(X_base_scaled)

    # decision_function: higher = more normal; predict: 1 = inlier, -1 = outlier.
    scores = model.decision_function(X_all_scaled)
    preds = model.predict(X_all_scaled)
    for w, s, p in zip(windows, scores, preds, strict=True):
        w.score = float(s)
        # Within the baseline window, never flag as anomaly (by construction).
        w.is_anomaly = bool(p == -1) and w.window_end_ns > baseline_end_ns
    return windows


def write_jsonl(path: Path, windows: list[WindowFeature]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for w in windows:
            fh.write(w.to_jsonl() + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--window-sec", type=float, default=5.0)
    p.add_argument("--baseline-sec", type=float, default=60.0)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--contamination", default="auto",
                   help="float in (0, 0.5) or 'auto'")
    args = p.parse_args()
    out = args.out or (args.run_dir / "anomalies.jsonl")

    cont = args.contamination
    try:
        cont = float(cont)
    except (TypeError, ValueError):
        pass

    windows = build_windows(args.run_dir, window_sec=args.window_sec)
    windows = detect(windows, baseline_sec=args.baseline_sec, contamination=cont)
    write_jsonl(out, windows)

    n_anom = sum(1 for w in windows if w.is_anomaly)
    print(f"wrote {len(windows)} windows ({n_anom} flagged) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
