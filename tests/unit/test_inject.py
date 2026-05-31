"""Unit tests for attacker.inject — payload structure + event emission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from attacker.inject import MALFORMED_PAYLOADS, emit_event


def test_malformed_payloads_have_distinct_names():
    names = [name for name, _ in MALFORMED_PAYLOADS]
    assert len(names) == len(set(names)), "duplicate payload names"


def test_malformed_payloads_non_empty():
    assert len(MALFORMED_PAYLOADS) >= 5
    for name, payload in MALFORMED_PAYLOADS:
        assert isinstance(payload, bytes)
        assert len(payload) > 0


def test_emit_event_writes_jsonl(tmp_path: Path):
    events = tmp_path / "chaos_events.jsonl"
    emit_event(events, "test_kind", "test_scenario", {"k": "v", "n": 42})
    emit_event(events, "second", "test_scenario")
    lines = events.read_text().strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    assert rec0["kind"] == "test_kind"
    assert rec0["scenario"] == "test_scenario"
    assert rec0["k"] == "v"
    assert rec0["n"] == 42
    assert "ts_ns" in rec0
    assert "ts_ns" in rec1
    # Timestamps should be monotonically non-decreasing.
    assert rec1["ts_ns"] >= rec0["ts_ns"]


def test_emit_event_creates_parent_dir(tmp_path: Path):
    events = tmp_path / "deep" / "nested" / "chaos_events.jsonl"
    emit_event(events, "k", "s")
    assert events.exists()
    lines = events.read_text().strip().splitlines()
    assert len(lines) == 1
