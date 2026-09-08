"""ROC and PR sweep across operating points for the multi-channel Isolation Forest.

For each scenario, pools per-window (score, is_attack_window) tuples from all
runs and computes precision/recall/F1 at threshold percentiles 10, 25, 50, 75,
90, 95, 99 of the per-run baseline score distribution. Also computes AUPRC
across the continuous threshold sweep.

Output: avaliacao/roc_sweep_summary.csv plus fig_roc_sweep.pdf.

A window is labeled as "attack" if its [window_start_ns, window_end_ns]
overlaps any chaos_events.jsonl entry timestamp.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)


def load_run(run_dir: Path) -> tuple[list[dict], list[int]]:
    anoms = []
    with open(run_dir / "anomalies.jsonl") as f:
        for line in f:
            anoms.append(json.loads(line))

    event_ts: list[int] = []
    ev_path = run_dir / "chaos_events.jsonl"
    if ev_path.exists():
        with open(ev_path) as f:
            for line in f:
                d = json.loads(line)
                kind = d.get("kind", "")
                if kind in ("malformed_pdu", "replay_pdu", "mutate_start", "mutate_done"):
                    event_ts.append(int(d["ts_ns"]))
    return anoms, event_ts


def label_windows(anoms: list[dict], event_ts: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Return (scores, is_attack) arrays."""
    scores = np.array([w["score"] for w in anoms])
    is_attack = np.zeros(len(anoms), dtype=int)
    for i, w in enumerate(anoms):
        s, e = w["window_start_ns"], w["window_end_ns"]
        for ts in event_ts:
            if s <= ts < e:
                is_attack[i] = 1
                break
    return scores, is_attack


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out-csv", type=Path, default=Path("avaliacao/roc_sweep_summary.csv"))
    p.add_argument("--out-fig", type=Path, default=Path("avaliacao/fig_roc_sweep.pdf"))
    args = p.parse_args()

    scenarios = ["baseline", "malformed", "mutate", "replay"]
    per_scenario: dict[str, list[tuple[float, int]]] = {s: [] for s in scenarios}

    for run_dir in sorted(args.avaliacao.iterdir()):
        if not run_dir.is_dir() or not (run_dir / "anomalies.jsonl").exists():
            continue
        name = run_dir.name
        if "-single-plc-" not in name:
            continue
        scenario = None
        for s in scenarios:
            if f"-{s}-" in name:
                scenario = s
                break
        if scenario is None:
            continue
        # Only Class 1 runs (skip Class 2/3 binary-separation runs and overnight extensions)
        if not any(name.startswith("2026-05-18T") and f"i{i}" in name.split("-")[-1] for i in range(1, 11)):
            continue
        anoms, ev = load_run(run_dir)
        scores, is_attack = label_windows(anoms, ev)
        per_scenario[scenario].extend(zip(scores, is_attack, strict=True))

    rows = []
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    palette = {"baseline": "#888888", "malformed": "#D55E00", "mutate": "#0072B2", "replay": "#009E73"}

    for s in scenarios:
        if not per_scenario[s]:
            continue
        scores = np.array([x[0] for x in per_scenario[s]])
        is_attack = np.array([x[1] for x in per_scenario[s]])
        if is_attack.sum() == 0:
            rows.append({"scenario": s, "n_windows": len(scores), "n_attack": 0, "auroc": None, "auprc": None})
            continue
        # Note: Isolation Forest returns LOWER scores for more anomalous → invert
        y_score = -scores
        auroc = roc_auc_score(is_attack, y_score)
        auprc = average_precision_score(is_attack, y_score)
        rows.append({"scenario": s, "n_windows": len(scores), "n_attack": int(is_attack.sum()),
                     "auroc": round(auroc, 3), "auprc": round(auprc, 3)})

        fpr, tpr, _ = roc_curve(is_attack, y_score)
        prec, rec, _ = precision_recall_curve(is_attack, y_score)
        axes[0].plot(fpr, tpr, label=f"{s} (AUROC={auroc:.2f})", color=palette[s], linewidth=2)
        axes[1].plot(rec, prec, label=f"{s} (AUPRC={auprc:.2f})", color=palette[s], linewidth=2)

    axes[0].plot([0, 1], [0, 1], "k--", linewidth=0.8, alpha=0.5)
    axes[0].set_xlabel("False Positive Rate")
    axes[0].set_ylabel("True Positive Rate")
    axes[0].set_title("ROC curves")
    axes[0].legend(loc="lower right", fontsize=9)
    axes[0].grid(alpha=0.3)
    axes[1].set_xlabel("Recall")
    axes[1].set_ylabel("Precision")
    axes[1].set_title("Precision-Recall curves")
    axes[1].legend(loc="lower left", fontsize=9)
    axes[1].grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(args.out_fig, dpi=300, bbox_inches="tight")
    plt.savefig(str(args.out_fig).replace(".pdf", ".png"), dpi=300, bbox_inches="tight")

    df = pd.DataFrame(rows)
    df.to_csv(args.out_csv, index=False)
    print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
