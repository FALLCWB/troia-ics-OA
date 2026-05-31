"""Killer-demo analysis (§V.G), three trigger classes.

Pre-registered in ``analysis/killer_demo_preregistration.md``. The analysis
plan is fixed before the data is collected; this script implements it.

Three classes from the canonical hardware-Trojan taxonomy (Section III.A
of the paper) are tested in parallel:

  Class 1 (data-conditioned)     : provoke_on    vs provoke_off
  Class 2 (sequence-conditioned) : provoke_seq_on  vs provoke_seq_off
  Class 3 (environment-conditioned): provoke_env_on  vs provoke_env_off

For each class we run a two-proportion z-test (two-tailed, alpha = 0.05),
Wilson 95% CIs per arm, and median manifestation latency for the treatment
arm. Manifestation is read from the class-specific counter:

  Class 1 -> integrity.summary.json:trigger_fired_max
  Class 2 -> integrity.summary.json:trigger_seq_fired_max
  Class 3 -> integrity.summary.json:trigger_env_fired_max
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from statistics import median

RUN_ID_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{6})-(?P<config>[a-z0-9-]+)-(?P<scenario>[a-z0-9_]+)-i(?P<iter>\d+)$"
)

# Each class is (display name, treatment scenario, control scenario,
# manifestation key in integrity.summary.json, first-ts key).
CLASSES = [
    ("class1_data", "provoke_on", "provoke_off",
     "trigger_fired_max", "trigger_fired_first_ts_ns"),
    ("class2_seq", "provoke_seq_on", "provoke_seq_off",
     "trigger_seq_fired_max", "trigger_seq_fired_first_ts_ns"),
    ("class3_env", "provoke_env_on", "provoke_env_off",
     "trigger_env_fired_max", "trigger_env_fired_first_ts_ns"),
]


def _summaries_for(avaliacao: Path, scenario: str, max_key: str, ts_key: str) -> list[dict]:
    out: list[dict] = []
    for d in sorted(avaliacao.iterdir()):
        if not d.is_dir():
            continue
        m = RUN_ID_RE.match(d.name)
        if not m or m.group("scenario") != scenario:
            continue
        sj = d / "integrity.summary.json"
        if not sj.exists():
            continue
        try:
            summary = json.loads(sj.read_text())
        except json.JSONDecodeError:
            continue
        run_start_ns = 0
        ce = d / "chaos_events.jsonl"
        if ce.exists():
            try:
                first_line = ce.read_text().splitlines()[0]
                run_start_ns = int(json.loads(first_line).get("ts_ns", 0))
            except (json.JSONDecodeError, IndexError, ValueError):
                pass
        manifested = int(summary.get(max_key, 0)) > 0
        out.append({
            "run_id": d.name,
            "manifested": manifested,
            "trigger_max": int(summary.get(max_key, 0)),
            "trigger_first_ts_ns": int(summary.get(ts_key, 0)),
            "run_start_ns": run_start_ns,
        })
    return out


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p_hat = k / n
    denom = 1 + (z**2) / n
    centre = (p_hat + (z**2) / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1 - p_hat) / n + (z**2) / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def _fisher_exact_two_sided(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher's exact for a 2x2 table.

    Table is [[k1, n1-k1], [k2, n2-k2]]. Marginals: row totals n1, n2;
    column totals k1+k2, (n1-k1)+(n2-k2). Uses the minimum-likelihood
    two-sided definition (consistent with scipy.stats.fisher_exact).
    Returns 1.0 when either arm is empty (cannot compute).
    """
    if n1 == 0 or n2 == 0:
        return 1.0
    from math import lgamma, exp
    n = n1 + n2
    k_total = k1 + k2

    def _logp(a: int) -> float:
        return (
            lgamma(n1 + 1) - lgamma(a + 1) - lgamma(n1 - a + 1)
            + lgamma(n2 + 1) - lgamma(k_total - a + 1) - lgamma(n2 - k_total + a + 1)
            - lgamma(n + 1) + lgamma(k_total + 1) + lgamma(n - k_total + 1)
        )

    a_min = max(0, k_total - n2)
    a_max = min(n1, k_total)
    obs_logp = _logp(k1)
    p_total = 0.0
    for a in range(a_min, a_max + 1):
        lp = _logp(a)
        if lp <= obs_logp + 1e-12:
            p_total += exp(lp)
    return min(1.0, p_total)


def _two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    if n1 == 0 or n2 == 0:
        return (0.0, 1.0)
    p1 = k1 / n1
    p2 = k2 / n2
    p_pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return (0.0, 1.0)
    z = (p1 - p2) / se
    p = math.erfc(abs(z) / math.sqrt(2))
    return (z, p)


def _per_class(avaliacao: Path, name: str, treat: str, ctrl: str,
               max_key: str, ts_key: str) -> dict:
    off = _summaries_for(avaliacao, ctrl, max_key, ts_key)
    on = _summaries_for(avaliacao, treat, max_key, ts_key)
    n_off, n_on = len(off), len(on)
    k_off = sum(1 for r in off if r["manifested"])
    k_on = sum(1 for r in on if r["manifested"])
    z, p_val = _two_proportion_z(k_on, n_on, k_off, n_off)
    p_fisher = _fisher_exact_two_sided(k_on, n_on, k_off, n_off)
    ci_off = _wilson_ci(k_off, n_off)
    ci_on = _wilson_ci(k_on, n_on)

    latencies_sec: list[float] = []
    for r in on:
        if r["manifested"] and r["run_start_ns"] and r["trigger_first_ts_ns"]:
            dt = (r["trigger_first_ts_ns"] - r["run_start_ns"]) / 1e9
            if dt > 0:
                latencies_sec.append(dt)
    median_latency = float(median(latencies_sec)) if latencies_sec else None

    return {
        "class": name,
        "treatment_scenario": treat,
        "control_scenario": ctrl,
        "arms": {
            ctrl: {
                "n": n_off, "manifested": k_off,
                "p_hat": round(k_off / n_off, 4) if n_off else None,
                "wilson_95ci": [round(ci_off[0], 4), round(ci_off[1], 4)],
            },
            treat: {
                "n": n_on, "manifested": k_on,
                "p_hat": round(k_on / n_on, 4) if n_on else None,
                "wilson_95ci": [round(ci_on[0], 4), round(ci_on[1], 4)],
                "median_manifestation_latency_sec": (
                    round(median_latency, 2) if median_latency is not None else None
                ),
            },
        },
        "z_stat": round(z, 4),
        "p_value": p_val,
        "reject_h0": p_val < 0.05,
        "p_fisher_exact": p_fisher,
        "reject_h0_fisher": p_fisher < 0.05,
        "raw_runs": {ctrl: off, treat: on},
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avaliacao", type=Path, default=Path("avaliacao"))
    p.add_argument("--out", type=Path, default=Path("avaliacao/killer_demo_summary.json"))
    args = p.parse_args()

    classes_summary = {}
    for name, treat, ctrl, max_key, ts_key in CLASSES:
        result = _per_class(args.avaliacao, name, treat, ctrl, max_key, ts_key)
        classes_summary[name] = result

    summary = {
        "preregistration": "analysis/killer_demo_preregistration.md",
        "alpha": 0.05,
        "test": "two-proportion z-test, two-tailed (per trigger class)",
        "classes": classes_summary,
    }
    # Also surface the Class 1 result at the top level so consumers built for
    # the single-class version of this analysis keep working.
    c1 = classes_summary["class1_data"]
    summary.update({
        "arms": c1["arms"],
        "z_stat": c1["z_stat"],
        "p_value": c1["p_value"],
        "reject_h0": c1["reject_h0"],
        "p_fisher_exact": c1["p_fisher_exact"],
        "reject_h0_fisher": c1["reject_h0_fisher"],
        "raw_runs": c1["raw_runs"],
    })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2))

    for name in ("class1_data", "class2_seq", "class3_env"):
        r = classes_summary[name]
        ctrl_n = r["arms"][r["control_scenario"]]["n"]
        on_n = r["arms"][r["treatment_scenario"]]["n"]
        ctrl_k = r["arms"][r["control_scenario"]]["manifested"]
        on_k = r["arms"][r["treatment_scenario"]]["manifested"]
        verdict = "REJECT H0" if r["reject_h0"] else "fail to reject H0"
        if ctrl_n == 0 and on_n == 0:
            print(f"[killer-demo:{name}] no runs found (skipped)")
            continue
        base_msg = (
            f"[killer-demo:{name}] {verdict}: "
            f"{r['treatment_scenario']}={on_k}/{on_n} vs "
            f"{r['control_scenario']}={ctrl_k}/{ctrl_n}, "
            f"z={r['z_stat']:.2f}, p={r['p_value']:.3g}"
        )
        if ctrl_n > 0 and on_n > 0:
            base_msg += f", fisher={r['p_fisher_exact']:.3g}"
        print(base_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
