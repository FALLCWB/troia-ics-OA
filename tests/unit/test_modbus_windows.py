"""Tests for the per-window output of metrics.modbus_parser and the
per-window malformed-rate lookup in monitor.detector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metrics.modbus_parser import WindowCounts
from monitor.detector import _per_window_malformed_rate


SEC = 1_000_000_000


def test_window_counts_malformed_rate_no_pdus():
    w = WindowCounts(window_start_ns=0, window_end_ns=SEC)
    assert w.malformed_rate == 0.0


def test_window_counts_malformed_rate_partial():
    w = WindowCounts(window_start_ns=0, window_end_ns=SEC, total_pdus=10, malformed=3)
    assert w.malformed_rate == pytest.approx(0.3)


def test_window_counts_jsonl_roundtrip():
    w = WindowCounts(window_start_ns=100, window_end_ns=100 + SEC,
                     total_pdus=20, malformed=4, unknown_fc=1, exceptions=2)
    decoded = json.loads(w.to_jsonl())
    assert decoded["total_pdus"] == 20
    assert decoded["malformed"] == 4
    assert decoded["unknown_fc"] == 1
    assert decoded["exceptions"] == 2
    assert decoded["malformed_rate"] == pytest.approx(0.2)


def test_detector_prefers_per_window_modbus_when_present(tmp_path: Path):
    # Per-window file with one matching record and one non-matching one.
    win_path = tmp_path / "modbus_windows.jsonl"
    win_path.write_text(
        json.dumps({"window_start_ns": 0, "window_end_ns": 5 * SEC,
                    "total_pdus": 100, "malformed": 25, "unknown_fc": 0,
                    "exceptions": 0, "malformed_rate": 0.25}) + "\n"
        + json.dumps({"window_start_ns": 5 * SEC, "window_end_ns": 10 * SEC,
                      "total_pdus": 50, "malformed": 1, "unknown_fc": 0,
                      "exceptions": 0, "malformed_rate": 0.02}) + "\n"
    )
    # Even with an aggregate file present, per-window wins.
    (tmp_path / "modbus.summary.json").write_text(
        json.dumps({"total_pdus": 150, "malformed": 26})
    )
    assert _per_window_malformed_rate(tmp_path, 0, 5 * SEC) == pytest.approx(0.25)
    assert _per_window_malformed_rate(tmp_path, 5 * SEC, 10 * SEC) == pytest.approx(0.02)


def test_detector_returns_zero_when_window_not_in_per_window_file(tmp_path: Path):
    (tmp_path / "modbus_windows.jsonl").write_text(
        json.dumps({"window_start_ns": 0, "window_end_ns": 5 * SEC,
                    "total_pdus": 10, "malformed": 1, "unknown_fc": 0,
                    "exceptions": 0, "malformed_rate": 0.1}) + "\n"
    )
    # Requesting a window not in the file returns 0.0 (no traffic that window).
    assert _per_window_malformed_rate(tmp_path, 100 * SEC, 105 * SEC) == 0.0


def test_detector_falls_back_to_aggregate_when_per_window_absent(tmp_path: Path):
    (tmp_path / "modbus.summary.json").write_text(
        json.dumps({"total_pdus": 200, "malformed": 50})
    )
    assert _per_window_malformed_rate(tmp_path, 0, 5 * SEC) == pytest.approx(0.25)


def test_detector_zero_when_no_modbus_files(tmp_path: Path):
    assert _per_window_malformed_rate(tmp_path, 0, 5 * SEC) == 0.0
