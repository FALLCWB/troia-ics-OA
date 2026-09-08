"""Regression tests for the aggregation defects found in the Access-2026-32283 audit.

Each test pins a behaviour that a shipped script got wrong at some point, so that the
same defect cannot return silently:

  * an empty run directory used to be scored as F1 = 0 and folded into the per-scenario
    mean, which depressed the reported replay figures;
  * the detector feature vector must stay exactly four columns, and the missed-read
    count must stay out of it;
  * the operating point must stay the library decision boundary, with no percentile
    threshold reintroduced;
  * an aggregation must report the sample size it actually consumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from analysis.rule_baseline import score_run_with_rules
from analysis.seed_sensitivity import score_run_with_seed
from monitor.detector import FEATURE_NAMES, build_windows, detect

SEC = 1_000_000_000


def _complete_run(base: Path, run_id: str, n_seconds: int = 120) -> Path:
    """A run directory with enough integrity samples to yield a usable baseline."""
    d = base / run_id
    d.mkdir(parents=True, exist_ok=True)
    samples = [
        {"ts_ns": i * SEC // 4, "setpoint": 500, "level": 500 - 147, "valve": 200,
         "error": 147, "alarm_cnt": 0, "alarm": False, "safety": False}
        for i in range(n_seconds * 4)
    ]
    (d / "integrity.jsonl").write_text("\n".join(json.dumps(s) for s in samples) + "\n")
    (d / "availability.jsonl").write_text(
        "\n".join(json.dumps({"ts_ns": i * SEC, "name": "troia-plc", "state": "running"})
                  for i in range(n_seconds)) + "\n")
    (d / "chaos_events.jsonl").write_text(json.dumps({"ts_ns": 90 * SEC, "kind": "malformed_pdu"}) + "\n")
    return d


# --- empty and unusable run directories -------------------------------------------

def test_rule_baseline_skips_empty_directory(tmp_path: Path):
    """An empty directory must be skipped, not scored as a missed detection."""
    empty = tmp_path / "2026-05-18T184723-single-plc-replay-i1"
    empty.mkdir()
    assert score_run_with_rules(empty, window_sec=5.0, baseline_sec=60.0,
                                k_sigma=2.0, tolerance_sec=2.5) is None


def test_seed_sensitivity_skips_empty_directory(tmp_path: Path):
    empty = tmp_path / "2026-05-18T185025-dual-plc-baseline-i1"
    empty.mkdir()
    assert score_run_with_seed(empty, seed=42, window_sec=5.0,
                               baseline_sec=60.0, tolerance_sec=2.5) is None


def test_run_without_baseline_window_is_rejected_not_zeroed(tmp_path: Path):
    """A run too short to fit a baseline window is rejected, not scored as zero.

    Returning 0.0 here is what let an unusable run enter a published mean.
    """
    short = _complete_run(tmp_path, "2026-05-18T000000-single-plc-replay-i9", n_seconds=20)
    assert score_run_with_rules(short, window_sec=5.0, baseline_sec=60.0,
                                k_sigma=2.0, tolerance_sec=2.5) is None
    assert score_run_with_seed(short, seed=42, window_sec=5.0,
                               baseline_sec=60.0, tolerance_sec=2.5) is None


def test_empty_directory_cannot_change_a_mean(tmp_path: Path):
    """The mean over one usable run must not move when an empty directory is added."""
    good = _complete_run(tmp_path, "2026-05-18T000000-single-plc-replay-i1", n_seconds=120)
    (tmp_path / "2026-05-18T111111-single-plc-replay-i2").mkdir()

    scored = [score_run_with_rules(d, window_sec=5.0, baseline_sec=60.0,
                                   k_sigma=2.0, tolerance_sec=2.5)
              for d in sorted(tmp_path.iterdir()) if d.is_dir()]
    kept = [s for s in scored if s is not None]
    assert len(scored) == 2 and len(kept) == 1, "the empty directory must not be scored"
    assert float(np.mean([s["f1"] for s in kept])) == kept[0]["f1"]


def test_aggregation_reports_the_n_it_consumed(tmp_path: Path):
    """n must count usable runs, so a silently added directory cannot inflate it."""
    for i in (1, 2, 3):
        _complete_run(tmp_path, f"2026-05-18T00000{i}-single-plc-replay-i{i}", n_seconds=120)
    (tmp_path / "2026-05-18T999999-single-plc-replay-i4").mkdir()

    kept = [r for r in (score_run_with_rules(d, window_sec=5.0, baseline_sec=60.0,
                                             k_sigma=2.0, tolerance_sec=2.5)
                        for d in sorted(tmp_path.iterdir()) if d.is_dir()) if r is not None]
    assert len(kept) == 3


# --- detector definition -----------------------------------------------------------

def test_feature_vector_is_exactly_four_channels():
    assert FEATURE_NAMES == (
        "latency_stddev_ms", "availability_pct", "integrity_dev_rms", "malformed_rate",
    ), "the article describes a four-dimensional feature vector"


@pytest.mark.parametrize("forbidden", ["missed", "missed_reads", "n_missed_reads",
                                       "safety", "safety_tripped", "restart", "restarts"])
def test_diagnostic_signals_stay_out_of_the_feature_vector(forbidden):
    """Missed reads, the safety flag and restart counts are observability outputs.

    The article states that the detector does not consume them; adding one would make
    Section V-E false.
    """
    assert not any(forbidden in name for name in FEATURE_NAMES)


def test_built_windows_carry_four_features(tmp_path: Path):
    d = _complete_run(tmp_path, "2026-05-18T000000-single-plc-baseline-i1", n_seconds=120)
    windows = build_windows(d, window_sec=5.0)
    assert windows, "fixture should produce windows"
    assert all(len(w.features) == len(FEATURE_NAMES) for w in windows)


def test_operating_point_is_the_library_boundary_not_a_percentile(tmp_path: Path):
    """Flags must follow IsolationForest.predict, not a percentile of the baseline scores.

    Section V-G described a 95th-percentile threshold that the code never implemented.
    """
    import inspect

    import monitor.detector as det

    src = inspect.getsource(det)
    assert "percentile" not in src.lower(), "no percentile threshold may be reintroduced"
    assert "contamination" in src

    d = _complete_run(tmp_path, "2026-05-18T000000-single-plc-baseline-i1", n_seconds=120)
    windows = detect(build_windows(d, window_sec=5.0), baseline_sec=60.0)
    baseline_end = windows[0].window_start_ns + 60 * SEC
    assert not any(w.is_anomaly for w in windows if w.window_end_ns <= baseline_end), \
        "warm-up windows are never flagged, by construction"
