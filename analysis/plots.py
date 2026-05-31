"""Plot the matrix from results.csv.

Produces three figures into ${OUT_DIR}/ (default: avaliacao/_plots/):
  1. box_uptime_by_scenario.png       — uptime% box plot per scenario
  2. box_integrity_by_scenario.png    — RMS deviation box plot per scenario
  3. heatmap_f1_config_scenario.png   — F1 mean per (config, scenario)
  4. confusion_per_scenario.png       — stacked bar TP/FP/FN/TN per scenario

Reads avaliacao/results.csv produced by analysis.aggregate.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _read(results_csv: Path) -> list[dict]:
    with results_csv.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _safe_float(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _group_values(rows: list[dict], key: str, value: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = _safe_float(r.get(value, ""))
        if v is None:
            continue
        out[r.get(key, "?")].append(v)
    return out


def box(rows: list[dict], value_col: str, ylabel: str, out_path: Path) -> None:
    groups = _group_values(rows, "scenario", value_col)
    if not groups:
        print(f"skip {out_path.name}: no data")
        return
    labels = sorted(groups)
    data = [groups[k] for k in labels]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.boxplot(data, labels=labels, showmeans=True)
    ax.set_xlabel("scenario")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


def heatmap_f1(rows: list[dict], out_path: Path) -> None:
    grid: dict[tuple[str, str], list[float]] = defaultdict(list)
    configs: set[str] = set()
    scenarios: set[str] = set()
    for r in rows:
        f1 = _safe_float(r.get("f1", ""))
        if f1 is None:
            continue
        cfg, sc = r.get("config", "?"), r.get("scenario", "?")
        grid[(cfg, sc)].append(f1)
        configs.add(cfg)
        scenarios.add(sc)
    if not grid:
        print(f"skip {out_path.name}: no F1 data")
        return
    configs_l = sorted(configs)
    scenarios_l = sorted(scenarios)
    mat = np.full((len(configs_l), len(scenarios_l)), np.nan)
    for (cfg, sc), values in grid.items():
        i, j = configs_l.index(cfg), scenarios_l.index(sc)
        mat[i, j] = float(np.mean(values))
    fig, ax = plt.subplots(figsize=(6, 4))
    im = ax.imshow(mat, cmap="viridis", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(scenarios_l)), scenarios_l)
    ax.set_yticks(range(len(configs_l)), configs_l)
    for i in range(len(configs_l)):
        for j in range(len(scenarios_l)):
            v = mat[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                        color="white" if v < 0.5 else "black", fontsize=9)
    ax.set_xlabel("scenario")
    ax.set_ylabel("config")
    fig.colorbar(im, ax=ax, label="mean F1")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


def confusion_stacked(rows: list[dict], out_path: Path) -> None:
    sums: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    for r in rows:
        sc = r.get("scenario", "?")
        for k in ("tp", "fp", "fn", "tn"):
            v = _safe_float(r.get(k, ""))
            if v is not None:
                sums[sc][k] += int(v)
    if not sums:
        print(f"skip {out_path.name}: no confusion data")
        return
    scenarios = sorted(sums)
    tp = [sums[s]["tp"] for s in scenarios]
    fp = [sums[s]["fp"] for s in scenarios]
    fn = [sums[s]["fn"] for s in scenarios]
    tn = [sums[s]["tn"] for s in scenarios]
    fig, ax = plt.subplots(figsize=(7, 4))
    bottom = np.zeros(len(scenarios))
    # Hatch patterns added as secondary accessibility encoding (colorblind + grayscale).
    for label, vals, color, hatch in [
        ("TP", tp, "#2a9d8f", ""),
        ("FN", fn, "#e76f51", "/"),
        ("FP", fp, "#f4a261", "\\"),
        ("TN", tn, "#264653", "x"),
    ]:
        bars = ax.bar(scenarios, vals, bottom=bottom, label=label, color=color,
                      hatch=hatch, edgecolor="white")
        for bar in bars:
            bar.set_hatch(hatch)
        bottom = bottom + np.asarray(vals)
    ax.set_xlabel("scenario")
    ax.set_ylabel("count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"wrote {out_path}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, default=Path("avaliacao/results.csv"))
    p.add_argument("--out-dir", type=Path, default=Path("avaliacao/_plots"))
    args = p.parse_args()
    rows = _read(args.results)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    box(rows, "uptime_pct", "Container uptime (%)", args.out_dir / "box_uptime_by_scenario.png")
    box(rows, "rms_deviation", "PV RMS deviation", args.out_dir / "box_integrity_by_scenario.png")
    heatmap_f1(rows, args.out_dir / "heatmap_f1_config_scenario.png")
    confusion_stacked(rows, args.out_dir / "confusion_per_scenario.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
