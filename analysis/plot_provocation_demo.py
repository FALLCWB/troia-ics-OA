"""Generate the hero figure (provocation_demo.png) for §V.G of the paper.

Reads avaliacao/killer_demo_summary.json (produced by killer_demo_analysis.py)
and the per-run integrity.summary.json files of each arm, and emits a two-
panel figure spanning all three trigger classes:

  Class 1 — data-conditioned:        provoke_off vs provoke_on
  Class 2 — sequence-conditioned:    provoke_seq_off vs provoke_seq_on
  Class 3 — environment-conditioned: provoke_env_off vs provoke_env_on

  (a) Manifestation rates, grouped bars (3 class-groups × 2 arms) with
      Wilson 95% CIs and per-class z/p inset annotations.
  (b) Per-run trigger-fired counter, grouped scatter (class-specific counter
      per arm), symmetric-log y-axis so zeros and large counts coexist.

This is the single most persuasive figure of the paper: across all three
classes, provocation forces the trigger to fire; without it, it does not.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import NamedTuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RUN_ID_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})-(?P<config>[a-z0-9-]+)-(?P<scenario>[a-z0-9_]+)-i(?P<iter>\d+)$"
)

COLOR_CTRL = "#888888"
COLOR_TREAT = "#1f77b4"

# ---------------------------------------------------------------------------
# Class registry
# ---------------------------------------------------------------------------

class TriggerClass(NamedTuple):
    label: str            # human-readable short label for axis ticks
    name: str             # display name for legend / title
    treat_scenario: str   # scenario name for treatment arm dirs
    ctrl_scenario: str    # scenario name for control arm dirs
    counter_key: str      # key in integrity.summary.json for fired counter
    manifest_key: str     # key in integrity.summary.json for manifestation bool
    summary_key: str      # key inside summary["classes"] in killer_demo_summary.json


CLASSES: list[TriggerClass] = [
    TriggerClass(
        label="C1",
        name="Class 1 — data-conditioned",
        treat_scenario="provoke_on",
        ctrl_scenario="provoke_off",
        counter_key="trigger_fired_max",
        manifest_key="manifested_data",
        summary_key="class1_data",
    ),
    TriggerClass(
        label="C2",
        name="Class 2 — sequence-conditioned",
        treat_scenario="provoke_seq_on",
        ctrl_scenario="provoke_seq_off",
        counter_key="trigger_seq_fired_max",
        manifest_key="manifested_seq",
        summary_key="class2_seq",
    ),
    TriggerClass(
        label="C3",
        name="Class 3 — environment-conditioned",
        treat_scenario="provoke_env_on",
        ctrl_scenario="provoke_env_off",
        counter_key="trigger_env_fired_max",
        manifest_key="manifested_env",
        summary_key="class3_env",
    ),
]

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _gather_arm(avaliacao: Path, scenario: str, counter_key: str, manifest_key: str) -> list[dict]:
    """Return one dict per run directory matching *scenario*."""
    out = []
    if not avaliacao.is_dir():
        return out
    for d in sorted(avaliacao.iterdir()):
        if not d.is_dir():
            continue
        m = RUN_ID_RE.match(d.name)
        if not m or m.group("scenario") != scenario:
            continue
        sj = d / "integrity.summary.json"
        if not sj.exists():
            continue
        s = json.loads(sj.read_text())
        # manifest_key may be a new field (manifested_data / manifested_seq / manifested_env)
        # or the legacy "manifested" field for Class 1 backward compat.
        manifested = bool(
            s.get(manifest_key, s.get("manifested", False))
        )
        out.append({
            "run_id": d.name,
            "manifested": manifested,
            "counter": int(s.get(counter_key, 0)),
        })
    return out


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI; returns (lo, hi) in [0,1]."""
    if n == 0:
        return (0.0, 0.0)
    p_hat = k / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


class ArmStats(NamedTuple):
    k: int
    n: int
    rate: float
    ci: tuple[float, float]
    runs: list[dict]


class ClassResult(NamedTuple):
    tc: TriggerClass
    ctrl: ArmStats
    treat: ArmStats
    z_stat: float
    p_value: float
    reject_h0: bool


def _build_class_result(tc: TriggerClass, avaliacao: Path, summary: dict) -> ClassResult | None:
    """Build ClassResult for one TriggerClass, blending summary JSON with per-run dirs."""
    ctrl_runs = _gather_arm(avaliacao, tc.ctrl_scenario, tc.counter_key, tc.manifest_key)
    treat_runs = _gather_arm(avaliacao, tc.treat_scenario, tc.counter_key, tc.manifest_key)

    n_ctrl, n_treat = len(ctrl_runs), len(treat_runs)
    if n_ctrl == 0 and n_treat == 0:
        return None  # skip entirely

    k_ctrl = sum(1 for r in ctrl_runs if r["manifested"])
    k_treat = sum(1 for r in treat_runs if r["manifested"])
    rate_ctrl = k_ctrl / n_ctrl if n_ctrl else 0.0
    rate_treat = k_treat / n_treat if n_treat else 0.0

    # Prefer CIs from summary JSON when available; fall back to Wilson recompute.
    cls_summary = summary.get("classes", {}).get(tc.summary_key, {})
    # Also accept top-level arms for Class 1 backward compat.
    arms_summary = cls_summary.get("arms", summary.get("arms", {}))

    ci_ctrl = tuple(arms_summary.get(tc.ctrl_scenario, {}).get("wilson_95ci", list(_wilson_ci(k_ctrl, n_ctrl))))
    ci_treat = tuple(arms_summary.get(tc.treat_scenario, {}).get("wilson_95ci", list(_wilson_ci(k_treat, n_treat))))

    z_stat = cls_summary.get("z_stat", summary.get("z_stat", 0.0) if tc.summary_key == "class1_data" else 0.0)
    p_value = cls_summary.get("p_value", summary.get("p_value", 1.0) if tc.summary_key == "class1_data" else 1.0)
    reject_h0 = cls_summary.get("reject_h0", summary.get("reject_h0", False) if tc.summary_key == "class1_data" else False)

    ctrl_stats = ArmStats(k=k_ctrl, n=n_ctrl, rate=rate_ctrl, ci=ci_ctrl, runs=ctrl_runs)
    treat_stats = ArmStats(k=k_treat, n=n_treat, rate=rate_treat, ci=ci_treat, runs=treat_runs)
    return ClassResult(tc=tc, ctrl=ctrl_stats, treat=treat_stats,
                       z_stat=z_stat, p_value=p_value, reject_h0=reject_h0)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _draw_panel_a(ax: plt.Axes, results: list[ClassResult]) -> None:
    """Grouped bar chart: 3 classes × 2 arms."""
    n_classes = len(results)
    group_width = 0.65
    bar_w = group_width / 2
    x_centers = np.arange(n_classes, dtype=float)

    offsets = [-bar_w / 2, bar_w / 2]
    arm_colors = [COLOR_CTRL, COLOR_TREAT]

    for arm_idx, (offset, color) in enumerate(zip(offsets, arm_colors)):
        for cls_idx, res in enumerate(results):
            arm = res.ctrl if arm_idx == 0 else res.treat
            x = x_centers[cls_idx] + offset
            # Draw bar; control bars have rate=0 so show a thin visible stripe
            bar_height = max(arm.rate, 0.012) if arm_idx == 0 and arm.rate == 0 else arm.rate
            ax.bar(x, bar_height, width=bar_w, color=color, edgecolor="black", linewidth=1.0,
                   label=("control (0/n)" if arm_idx == 0 else "treatment") if cls_idx == 0 else "_nolegend_",
                   alpha=0.85 if arm_idx == 0 else 1.0)
            lo, hi = arm.ci
            yerr_lo = max(0.0, arm.rate - lo)
            yerr_hi = max(0.0, hi - arm.rate)
            ax.errorbar(x, arm.rate, yerr=[[yerr_lo], [yerr_hi]],
                        fmt="none", ecolor="black", capsize=5, lw=1.1)
            # Annotate treatment bars only
            if arm_idx == 1:
                pct = arm.rate * 100
                ax.text(x, arm.rate + 0.04,
                        f"{arm.k}/{arm.n}\n({pct:.0f}%)",
                        ha="center", fontsize=8, fontweight="bold")

    # Per-class z/p inset annotations
    for cls_idx, res in enumerate(results):
        pval_str = f"{res.p_value:.2g}" if res.p_value >= 1e-15 else "< 10⁻¹⁵"
        ax.text(x_centers[cls_idx], 1.25,
                f"z={res.z_stat:.2f}\np={pval_str}",
                ha="center", va="center", fontsize=7, style="italic",
                bbox=dict(boxstyle="round,pad=0.20", facecolor="#fff8e0",
                          edgecolor="#c08020", alpha=0.92))

    ax.set_xlim(-0.6, n_classes - 0.4)
    ax.set_ylim(0, 1.45)
    ax.set_xticks(x_centers)
    ax.set_xticklabels([res.tc.label for res in results], fontsize=9)
    ax.set_ylabel("Manifestation rate", fontsize=9)
    ax.set_title("(a) Trigger manifests only under reconstructed context", fontsize=9.5, loc="left")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.7)


def _draw_panel_b(ax: plt.Axes, results: list[ClassResult], summary: dict) -> None:
    """Grouped scatter: per-run trigger counter, one dot per run per class+arm.

    Also draws a faint horizontal annotation band per class showing the median
    manifestation latency from summary["classes"][key]["arms"][treat_scenario]
    ["median_manifestation_latency_sec"].
    """
    rng = np.random.default_rng(0)
    class_colors = ["#009E73", "#D55E00", "#CC79A7"]  # Okabe-Ito: Bluish Green, Vermillion, Reddish Purple
    class_markers = ["o", "s", "^"]

    # x positions: for 3 classes, group spacing of 3 with ctrl/treat columns per class
    group_gap = 3.5
    col_sep = 1.0

    x_ticks = []
    x_labels = []

    for cls_idx, res in enumerate(results):
        x_base = cls_idx * group_gap
        col = class_colors[cls_idx % len(class_colors)]
        mk = class_markers[cls_idx % len(class_markers)]
        label_prefix = res.tc.label

        for arm_idx, (arm, arm_label, fill) in enumerate(
            [(res.ctrl, "ctrl", "none"), (res.treat, "treat", col)]
        ):
            x_center = x_base + arm_idx * col_sep
            jitter = rng.normal(0, 0.08, size=len(arm.runs))
            ys = [r["counter"] for r in arm.runs]
            ax.scatter(
                x_center + jitter, ys,
                color=col if arm_idx == 1 else COLOR_CTRL,
                marker=mk,
                edgecolor="black", s=60, alpha=0.82,
                label=f"{label_prefix} {arm_label}" if cls_idx == 0 else "_nolegend_",
            )
            x_ticks.append(x_center)
            x_labels.append(f"{label_prefix}\n{arm_label}")

        # Median manifestation latency annotation (treatment arm only).
        # Drawn as a faint horizontal line near y=0 (linthresh boundary of symlog)
        # with a text label beside the treatment column.
        cls_summary = summary.get("classes", {}).get(res.tc.summary_key, {})
        # Also accept top-level arms for Class 1 backward compat.
        arms_summary = cls_summary.get("arms", summary.get("arms", {}))
        treat_arm_summary = arms_summary.get(res.tc.treat_scenario, {})
        median_lat = treat_arm_summary.get("median_manifestation_latency_sec", None)
        if median_lat is not None:
            x_label = x_base + col_sep / 2  # centered on the class group
            # Use axes-fraction y so the label sits at 92% height regardless of data range
            ax.text(
                x_label, 0.92,
                f"med. lat.\n{float(median_lat):.1f} s",
                ha="center", va="top", fontsize=7,
                color=col, fontweight="bold",
                transform=ax.get_xaxis_transform(),
                bbox=dict(boxstyle="round,pad=0.20", facecolor="white",
                          edgecolor=col, alpha=0.90, linewidth=0.8),
            )

    ax.axhline(0, color="#666", lw=0.8, linestyle=":")
    ax.set_xticks(x_ticks)
    ax.set_xticklabels(x_labels, fontsize=8, rotation=20, ha="right")
    ax.set_yscale("symlog", linthresh=1)
    ax.set_ylabel("trigger_fired counter at run end (symlog)", fontsize=9)
    ax.set_title("(b) Per-run trigger counter, one dot per run", fontsize=9.5, loc="left")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Custom legend: one entry per class
    legend_handles = []
    for cls_idx, res in enumerate(results):
        col = class_colors[cls_idx % len(class_colors)]
        mk = class_markers[cls_idx % len(class_markers)]
        legend_handles.append(
            plt.Line2D([0], [0], marker=mk, color="w", markerfacecolor=col,
                       markeredgecolor="black", markersize=8, label=res.tc.name)
        )
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("/opt/troia/avaliacao"))
    p.add_argument("--summary-json", type=Path, default=Path("/opt/troia/avaliacao/killer_demo_summary.json"))
    p.add_argument("--out", type=Path, default=Path("/opt/troia/avaliacao/_plots/provocation_demo.png"))
    args = p.parse_args()

    summary = json.loads(args.summary_json.read_text()) if args.summary_json.exists() else {}

    results: list[ClassResult] = []
    for tc in CLASSES:
        res = _build_class_result(tc, args.avaliacao, summary)
        if res is None:
            print(f"[plot] skipping {tc.name}: no runs found in either arm")
            continue
        results.append(res)

    if not results:
        print("[plot] ERROR: no class had any runs — nothing to plot")
        return 1

    # Build suptitle with per-class n counts
    parts = []
    for res in results:
        parts.append(f"{res.ctrl.n}+{res.treat.n}")
    title_counts = " | ".join(parts)
    suptitle = f"Binary-separation experiment: provocation forces manifestation across three trigger classes (n = {title_counts})"

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6.7, 8.0), constrained_layout=True)
    _draw_panel_a(ax1, results)
    _draw_panel_b(ax2, results, summary)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
