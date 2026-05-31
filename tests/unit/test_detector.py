"""Unit tests for monitor.detector — windowing and Isolation Forest behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from monitor.detector import (
    FEATURE_NAMES,
    WindowFeature,
    build_windows,
    detect,
)


SEC = 1_000_000_000


def _write_integrity(run_dir: Path, samples: list[dict]) -> None:
    p = run_dir / "integrity.jsonl"
    p.write_text("\n".join(json.dumps(s) for s in samples) + "\n")


def _write_availability(run_dir: Path, samples: list[dict]) -> None:
    p = run_dir / "availability.jsonl"
    p.write_text("\n".join(json.dumps(s) for s in samples) + "\n")


def test_build_windows_returns_empty_when_no_integrity(tmp_path: Path):
    assert build_windows(tmp_path) == []


def test_build_windows_buckets_samples(tmp_path: Path):
    samples = []
    for t in range(20):
        samples.append({"ts_ns": t * SEC, "setpoint": 500, "level": 500, "valve": 200,
                        "error": 0, "alarm_cnt": 0, "alarm": False, "safety": False})
    _write_integrity(tmp_path, samples)
    windows = build_windows(tmp_path, window_sec=5.0)
    assert len(windows) == 4   # 20 s into 4 windows of 5 s
    # Each feature vector has the expected number of features.
    for w in windows:
        assert len(w.features) == len(FEATURE_NAMES)


def test_integrity_dev_rms_zero_for_perfectly_tracked_setpoint(tmp_path: Path):
    samples = [
        {"ts_ns": i * SEC, "setpoint": 500, "level": 500, "valve": 200,
         "error": 0, "alarm_cnt": 0, "alarm": False, "safety": False}
        for i in range(10)
    ]
    _write_integrity(tmp_path, samples)
    windows = build_windows(tmp_path, window_sec=5.0)
    feature_map = dict(zip(FEATURE_NAMES, windows[0].features, strict=True))
    assert feature_map["integrity_dev_rms"] == pytest.approx(0.0)


def test_integrity_dev_rms_nonzero_when_level_drifts(tmp_path: Path):
    samples = [
        {"ts_ns": i * SEC, "setpoint": 500, "level": 500 + (i * 10), "valve": 200,
         "error": -i * 10, "alarm_cnt": 0, "alarm": False, "safety": False}
        for i in range(10)
    ]
    _write_integrity(tmp_path, samples)
    windows = build_windows(tmp_path, window_sec=5.0)
    feature_map = dict(zip(FEATURE_NAMES, windows[0].features, strict=True))
    assert feature_map["integrity_dev_rms"] > 0.0


def test_availability_pct_computed_from_state_field(tmp_path: Path):
    integ = [{"ts_ns": i * SEC, "setpoint": 500, "level": 500, "valve": 0,
              "error": 0, "alarm_cnt": 0, "alarm": False, "safety": False}
             for i in range(10)]
    avail = [
        {"ts_ns": 0,             "name": "plc", "state": "running", "restart_count": 0, "pid": 1},
        {"ts_ns": 1 * SEC,       "name": "plc", "state": "running", "restart_count": 0, "pid": 1},
        {"ts_ns": 2 * SEC,       "name": "plc", "state": "restarting", "restart_count": 1, "pid": 0},
        {"ts_ns": 3 * SEC,       "name": "plc", "state": "running", "restart_count": 1, "pid": 2},
    ]
    _write_integrity(tmp_path, integ)
    _write_availability(tmp_path, avail)
    windows = build_windows(tmp_path, window_sec=5.0)
    feature_map = dict(zip(FEATURE_NAMES, windows[0].features, strict=True))
    # 3 of 4 samples running in the first 5 s window.
    assert feature_map["availability_pct"] == pytest.approx(75.0)


def _wf(feats: list[float], t_start: int, t_end: int) -> WindowFeature:
    return WindowFeature(window_start_ns=t_start, window_end_ns=t_end, features=feats)


def test_detect_returns_empty_on_empty_input():
    assert detect([], baseline_sec=10) == []


def test_detect_silent_when_baseline_too_short():
    windows = [_wf([0, 100, 0, 0], 0, SEC), _wf([0, 100, 0, 0], SEC, 2 * SEC)]
    out = detect(windows, baseline_sec=10)
    assert all(not w.is_anomaly for w in out)


def test_detect_flags_outlier_after_baseline():
    # Baseline: 10 windows of (10ms stddev, 100% up, 5 dev rms, 0 malformed rate)
    base = [_wf([10.0, 100.0, 5.0, 0.0], i * SEC, (i + 1) * SEC) for i in range(10)]
    # After baseline: window with hugely different vector should be flagged.
    after = [_wf([800.0, 70.0, 300.0, 0.5], (10 + i) * SEC, (11 + i) * SEC) for i in range(3)]
    windows = base + after
    out = detect(windows, baseline_sec=10.0)
    # First 10 belong to baseline, never flagged.
    assert not any(w.is_anomaly for w in out[:10])
    # At least one of the 3 outlier-shaped windows is flagged.
    assert any(w.is_anomaly for w in out[10:])


def test_baseline_windows_are_never_flagged():
    base = [_wf([10.0, 100.0, 5.0, 0.0], i * SEC, (i + 1) * SEC) for i in range(10)]
    out = detect(base, baseline_sec=20.0)
    assert all(not w.is_anomaly for w in out)
