"""Aggregate per-run summaries into a single multi-metric results.csv.

Walks ${AVALIACAO_DIR}/* (default: ./avaliacao/), reads per-run side-car JSON
files written by metrics/* and the chaos_events.jsonl emitted by attacker, and
produces:

  - results.csv  (one row per run, all metrics joined)
  - plus the union of distinct configs/scenarios as side-info.

Columns:
  run_id, config, scenario, iter,
  uptime_pct, restarts_during_run,
  rms_deviation, max_abs_deviation, alarms_triggered, safety_tripped, unsafe,
  total_pdus, malformed_pdus, unknown_fc, exceptions,
  tp, fp, fn, tn, precision, recall, f1, fpr,
  events_count, anomalies_flagged_count, n_dumps

The run_id pattern set by scripts/run_matrix.sh is:
  <YYYY-MM-DDTHHMMSS>-<config>-<scenario>-i<iter>
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


RUN_ID_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})-(?P<config>[a-z0-9-]+)-(?P<scenario>[a-z0-9-]+)-i(?P<iter>\d+)$"
)


COLUMNS = [
    "run_id", "config", "scenario", "iter",
    "uptime_pct", "restarts_during_run",
    "rms_deviation", "max_abs_deviation", "alarms_triggered", "safety_tripped", "unsafe",
    "total_pdus", "malformed_pdus", "unknown_fc", "exceptions",
    "tp", "fp", "fn", "tn", "precision", "recall", "f1", "fpr",
    "events_count", "anomalies_flagged_count", "n_dumps",
]


def _load_json(p: Path) -> dict:
    if not p.exists() or p.stat().st_size == 0:
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _count_lines(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(1 for _ in p.open("r", encoding="utf-8") if _.strip())


def _count_flagged_anomalies(p: Path) -> int:
    if not p.exists():
        return 0
    n = 0
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("is_anomaly"):
                n += 1
    return n


def collect_one(run_dir: Path) -> dict | None:
    m = RUN_ID_RE.match(run_dir.name)
    if not m:
        return None
    # Skip runs that produced no collector output at all (e.g. an aborted
    # iteration where the PLC was unreachable). Require at least one of the
    # per-channel summary files to exist.
    has_any_data = any(
        (run_dir / name).exists()
        for name in (
            "availability.summary.json",
            "integrity.summary.json",
            "modbus.summary.json",
            "anomalies.jsonl",
        )
    )
    if not has_any_data:
        return None
    row = {col: "" for col in COLUMNS}
    row["run_id"] = run_dir.name
    row["config"] = m.group("config")
    row["scenario"] = m.group("scenario")
    row["iter"] = int(m.group("iter"))

    # availability.summary.json — {container_name: {uptime_pct, restarts_during_run, ...}}
    avail = _load_json(run_dir / "availability.summary.json")
    if avail:
        # Aggregate across containers: average uptime, sum restarts.
        uptimes = [v["uptime_pct"] for v in avail.values() if "uptime_pct" in v]
        restarts = sum(v.get("restarts_during_run", 0) for v in avail.values())
        if uptimes:
            row["uptime_pct"] = round(sum(uptimes) / len(uptimes), 3)
        row["restarts_during_run"] = restarts

    # integrity.summary.json
    integ = _load_json(run_dir / "integrity.summary.json")
    for k in ("rms_deviation", "max_abs_deviation", "alarms_triggered", "safety_tripped", "unsafe"):
        if k in integ:
            row[k] = integ[k]

    # modbus.summary.json
    mb = _load_json(run_dir / "modbus.summary.json")
    if "total_pdus" in mb:
        row["total_pdus"] = mb["total_pdus"]
        row["malformed_pdus"] = mb.get("malformed", 0)
        row["unknown_fc"] = mb.get("unknown_fc", 0)
        row["exceptions"] = mb.get("exceptions", 0)

    # scoring.summary.json
    sc = _load_json(run_dir / "scoring.summary.json")
    for k in ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "fpr"):
        if k in sc:
            row[k] = sc[k]

    # raw counts
    row["events_count"] = _count_lines(run_dir / "chaos_events.jsonl")
    row["anomalies_flagged_count"] = _count_flagged_anomalies(run_dir / "anomalies.jsonl")
    row["n_dumps"] = _count_lines(run_dir / "anomalies.jsonl")

    return row


def aggregate(avaliacao_dir: Path, out_csv: Path) -> int:
    rows: list[dict] = []
    for entry in sorted(avaliacao_dir.iterdir()):
        if not entry.is_dir():
            continue
        row = collect_one(entry)
        if row is not None:
            rows.append(row)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return len(rows)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out", type=Path, default=Path("avaliacao/results.csv"))
    args = p.parse_args()
    aggregate(args.avaliacao, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
