"""Tests for analysis.aggregate — directory walk + JSON parsing + CSV output."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from analysis.aggregate import RUN_ID_RE, aggregate, collect_one


def _make_run(base: Path, run_id: str, *, integ=None, sc=None, mb=None, avail=None,
              events_lines=0, anom_lines=0, anom_flagged=0):
    d = base / run_id
    d.mkdir(parents=True, exist_ok=True)
    if integ:
        (d / "integrity.summary.json").write_text(json.dumps(integ))
    if sc:
        (d / "scoring.summary.json").write_text(json.dumps(sc))
    if mb:
        (d / "modbus.summary.json").write_text(json.dumps(mb))
    if avail:
        (d / "availability.summary.json").write_text(json.dumps(avail))
    if events_lines:
        (d / "chaos_events.jsonl").write_text("\n".join(
            json.dumps({"ts_ns": i, "kind": "x", "scenario": "y"}) for i in range(events_lines)
        ) + "\n")
    if anom_lines:
        lines = []
        for i in range(anom_lines):
            flagged = i < anom_flagged
            lines.append(json.dumps({"timestamp_ns": i, "is_anomaly": flagged, "score": -1.0, "pid": 1, "features": {}}))
        (d / "anomalies.jsonl").write_text("\n".join(lines) + "\n")


def test_run_id_regex_matches_expected_pattern():
    m = RUN_ID_RE.match("2026-05-17T210000-single-plc-malformed-i7")
    assert m is not None
    assert m.group("config") == "single-plc"
    assert m.group("scenario") == "malformed"
    assert m.group("iter") == "7"


def test_run_id_regex_rejects_malformed():
    assert RUN_ID_RE.match("not-a-run") is None
    assert RUN_ID_RE.match("2026-05-17T210000-no-scenario") is None


def test_collect_one_assembles_row(tmp_path: Path):
    _make_run(
        tmp_path,
        "2026-05-17T210000-single-plc-malformed-i1",
        integ={"rms_deviation": 42.5, "max_abs_deviation": 120, "alarms_triggered": 3, "safety_tripped": False, "unsafe": False},
        sc={"tp": 8, "fp": 1, "fn": 2, "tn": 100, "precision": 0.888, "recall": 0.8, "f1": 0.842, "fpr": 0.0099},
        mb={"total_pdus": 500, "malformed": 25, "unknown_fc": 3, "exceptions": 1},
        avail={"troia-plc": {"uptime_pct": 99.5, "restarts_during_run": 0},
               "troia-hmi": {"uptime_pct": 100.0, "restarts_during_run": 1}},
        events_lines=10,
        anom_lines=200,
        anom_flagged=12,
    )
    row = collect_one(tmp_path / "2026-05-17T210000-single-plc-malformed-i1")
    assert row is not None
    assert row["config"] == "single-plc"
    assert row["scenario"] == "malformed"
    assert row["iter"] == 1
    assert row["uptime_pct"] == 99.75   # mean of 99.5 and 100.0
    assert row["restarts_during_run"] == 1
    assert row["rms_deviation"] == 42.5
    assert row["malformed_pdus"] == 25
    assert row["f1"] == 0.842
    assert row["events_count"] == 10
    assert row["anomalies_flagged_count"] == 12
    assert row["n_dumps"] == 200


def test_aggregate_writes_csv_with_only_well_formed_runs(tmp_path: Path):
    avaliacao = tmp_path / "avaliacao"
    _make_run(avaliacao, "2026-05-17T210000-single-plc-baseline-i1",
              integ={"rms_deviation": 12.0}, anom_lines=5)
    _make_run(avaliacao, "2026-05-17T210100-dual-plc-mutate-i1",
              integ={"rms_deviation": 88.0, "unsafe": True})
    # A bogus dir that shouldn't be picked up.
    (avaliacao / "not_a_run_dir").mkdir()
    out = tmp_path / "out.csv"
    n = aggregate(avaliacao, out)
    assert n == 2
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    scenarios = sorted(r["scenario"] for r in rows)
    assert scenarios == ["baseline", "mutate"]
