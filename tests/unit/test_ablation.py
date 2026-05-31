"""Unit tests for analysis.ablation — variant runner + aggregation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from analysis.ablation import VARIANTS, RUN_ID_RE, collect, summarise


SEC = 1_000_000_000


def _write_run(base: Path, run_id: str):
    d = base / run_id
    d.mkdir(parents=True, exist_ok=True)
    # 20 integrity samples spread over 20 s: nominal first 10, deviated next 10.
    samples = []
    for i in range(10):
        samples.append({"ts_ns": i * SEC, "setpoint": 500, "level": 500, "valve": 200,
                        "error": 0, "alarm_cnt": 0, "alarm": False, "safety": False})
    for i in range(10, 20):
        samples.append({"ts_ns": i * SEC, "setpoint": 500, "level": 500 + (i - 10) * 30, "valve": 0,
                        "error": -(i - 10) * 30, "alarm_cnt": 1, "alarm": True, "safety": False})
    (d / "integrity.jsonl").write_text("\n".join(json.dumps(s) for s in samples) + "\n")
    # One chaos event in the attack region.
    (d / "chaos_events.jsonl").write_text(json.dumps({"ts_ns": 14 * SEC, "kind": "deviate"}) + "\n")


def test_run_id_regex_recognises_format():
    assert RUN_ID_RE.match("2026-05-18T010203-single-plc-mutate-i3") is not None
    assert RUN_ID_RE.match("not-a-run") is None


def test_variants_have_distinct_names():
    names = [v.name for v in VARIANTS]
    assert len(names) == len(set(names))


def test_variants_cover_per_channel_and_multi():
    indices = [v.feature_indices for v in VARIANTS]
    # multi-channel variants have feature_indices=None
    assert None in indices
    # single-channel variants cover all four feature columns
    singles = [v.feature_indices for v in VARIANTS if v.feature_indices is not None]
    flat = [i for tpl in singles for i in tpl]
    assert sorted(set(flat)) == [0, 1, 2, 3]


def test_collect_produces_one_row_per_run_variant(tmp_path: Path):
    avaliacao = tmp_path / "avaliacao"
    avaliacao.mkdir()
    _write_run(avaliacao, "2026-05-18T000000-single-plc-baseline-i1")
    _write_run(avaliacao, "2026-05-18T000100-single-plc-mutate-i1")
    out = tmp_path / "ablation.csv"
    n = collect(avaliacao, out, window_sec=5.0, baseline_sec=10.0, tolerance_sec=2.5)
    # 2 runs × 6 variants = 12 rows
    assert n == 12
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 12
    variants_seen = {r["variant"] for r in rows}
    assert variants_seen == {v.name for v in VARIANTS}


def test_summarise_aggregates_by_scenario_variant(tmp_path: Path):
    avaliacao = tmp_path / "avaliacao"
    avaliacao.mkdir()
    for i in range(1, 4):
        _write_run(avaliacao, f"2026-05-18T00000{i}-single-plc-mutate-i{i}")
    out = tmp_path / "ablation.csv"
    summary = tmp_path / "ablation_summary.csv"
    collect(avaliacao, out, window_sec=5.0, baseline_sec=10.0, tolerance_sec=2.5)
    summarise(out, summary)
    with summary.open() as fh:
        rows = list(csv.DictReader(fh))
    # 1 scenario × 6 variants = 6 summary rows
    assert len(rows) == 6
    for row in rows:
        assert int(row["n"]) == 3
        assert row["scenario"] == "mutate"
