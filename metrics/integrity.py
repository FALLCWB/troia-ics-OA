"""Process-variable integrity metric.

Polls tank_pid.st holding registers via Modbus/TCP, computes deviation from
the expected steady-state trajectory:
  - setpoint (HR0) stable at user-supplied value (default 500)
  - level (HR1) within ±50 of setpoint after warmup (10 s)

Outputs JSONL per sample + summary with RMS deviation, max deviation, and
whether any alarm/safety bit triggered during the run.

Usage:
    python -m metrics.integrity \\
        --host plc --port 502 \\
        --out avaliacao/<run>/integrity.jsonl \\
        --interval 0.1 --duration 300 --setpoint 500 --warmup 10
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pymodbus.client import ModbusTcpClient


@dataclass(slots=True)
class IntegritySample:
    ts_ns: int
    setpoint: int
    level: int
    valve: int
    error: int
    alarm_cnt: int
    alarm: bool
    safety: bool
    trigger_fired: int = 0
    trigger_seq_fired: int = 0
    trigger_env_fired: int = 0

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self))


@dataclass(slots=True)
class IntegritySummary:
    n_samples: int = 0
    n_missed_reads: int = 0
    n_after_warmup: int = 0
    sum_sq_deviation: float = 0.0
    max_abs_deviation: int = 0
    alarms_triggered: int = 0
    safety_tripped: bool = False
    distinct_setpoints: set[int] = field(default_factory=set)
    trigger_fired_max: int = 0
    trigger_fired_first_ts_ns: int = 0
    trigger_seq_fired_max: int = 0
    trigger_seq_fired_first_ts_ns: int = 0
    trigger_env_fired_max: int = 0
    trigger_env_fired_first_ts_ns: int = 0

    @property
    def rms_deviation(self) -> float:
        return math.sqrt(self.sum_sq_deviation / self.n_after_warmup) if self.n_after_warmup else 0.0

    @property
    def manifested(self) -> bool:
        """True iff any of the three hidden triggers in tank_pid.st (Class 1
        data-conditioned, Class 2 sequence-conditioned, Class 3
        environment-conditioned) activated during this run."""
        return self.manifested_data or self.manifested_seq or self.manifested_env

    @property
    def manifested_data(self) -> bool:
        """Class 1 (data-conditioned) trigger fired."""
        return self.trigger_fired_max > 0

    @property
    def manifested_seq(self) -> bool:
        """Class 2 (sequence-conditioned) trigger fired."""
        return self.trigger_seq_fired_max > 0

    @property
    def manifested_env(self) -> bool:
        """Class 3 (environment-conditioned) trigger fired."""
        return self.trigger_env_fired_max > 0

    @property
    def unsafe(self) -> bool:
        # The natural P-controller steady-state offset is ~135 (15.0 %). The
        # "unsafe" threshold is set above that floor so it flags only excursions
        # caused by a trigger scenario, not nominal operation.
        return self.rms_deviation > 220.0 or self.safety_tripped or self.manifested

    def to_dict(self) -> dict:
        return {
            "n_samples": self.n_samples,
            "n_missed_reads": self.n_missed_reads,
            "n_after_warmup": self.n_after_warmup,
            "rms_deviation": round(self.rms_deviation, 3),
            "max_abs_deviation": self.max_abs_deviation,
            "alarms_triggered": self.alarms_triggered,
            "safety_tripped": self.safety_tripped,
            "distinct_setpoints": sorted(self.distinct_setpoints),
            "trigger_fired_max": self.trigger_fired_max,
            "trigger_fired_first_ts_ns": self.trigger_fired_first_ts_ns,
            "trigger_seq_fired_max": self.trigger_seq_fired_max,
            "trigger_seq_fired_first_ts_ns": self.trigger_seq_fired_first_ts_ns,
            "trigger_env_fired_max": self.trigger_env_fired_max,
            "trigger_env_fired_first_ts_ns": self.trigger_env_fired_first_ts_ns,
            "manifested": self.manifested,
            "manifested_data": self.manifested_data,
            "manifested_seq": self.manifested_seq,
            "manifested_env": self.manifested_env,
            "unsafe": self.unsafe,
        }


def _read(client: ModbusTcpClient) -> IntegritySample | None:
    # %MW0..%MW14 of tank_pid.st:
    #   0..7  -- sp, pv, cv, err_abs, k_p, k_i, k_d, alarm_cnt
    #   8..10 -- Class 1 trigger: context_a, context_b, trigger_fired counter
    #   11..12 -- Class 2 trigger: magic_seq, trigger_seq_fired counter
    #   13..14 -- Class 3 trigger: trigger_env_fired counter, env_state_dbg
    # OpenPLC v3 maps %MW0 to Modbus holding register 1024.
    rr_h = client.read_holding_registers(address=1024, count=15)
    rr_c = client.read_coils(address=0, count=2)
    if rr_h.isError() or rr_c.isError():
        return None
    hr = rr_h.registers
    coils = rr_c.bits
    return IntegritySample(
        ts_ns=time.time_ns(),
        setpoint=hr[0],
        level=hr[1],
        valve=hr[2],
        error=hr[3],
        alarm_cnt=hr[7],
        alarm=bool(coils[0]),
        safety=bool(coils[1]),
        trigger_fired=hr[10],
        trigger_seq_fired=hr[12],
        trigger_env_fired=hr[13],
    )


def run(host: str, port: int, out_path: Path, interval: float, duration: float, warmup_sec: float) -> IntegritySummary:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = IntegritySummary()
    stop = False

    def _stop(_s, _f):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    def _connect() -> ModbusTcpClient:
        c = ModbusTcpClient(host, port=port, timeout=2.0)
        c.connect()
        return c

    client = _connect()
    start = time.monotonic()
    deadline = start + duration if duration > 0 else float("inf")

    def _flush_summary() -> None:
        # Write a fresh summary file. Called both periodically (so we have a
        # valid summary even if the process is killed mid-run) and at exit.
        out_path.with_suffix(".summary.json").write_text(
            json.dumps(summary.to_dict(), indent=2)
        )

    last_summary_write = 0.0
    try:
        with out_path.open("a", encoding="utf-8") as fh:
            while not stop and time.monotonic() < deadline:
                loop_start = time.monotonic()
                # The PLC may be restarted mid-run (mutate scenario). Any
                # pymodbus exception, an empty response, or a connection-reset
                # should be treated as a sample loss, not a fatal error: log
                # nothing for this tick, sleep, and retry. If multiple ticks
                # fail in a row, re-open the socket so the recovery path
                # (post-mutate /start_plc click) can be picked up immediately.
                sample = None
                try:
                    sample = _read(client)
                except Exception:
                    sample = None
                if sample is None:
                    summary.n_missed_reads += 1
                    if summary.n_missed_reads % 20 == 19:
                        # Connection likely dead; rebuild the client.
                        try:
                            client.close()
                        except Exception:
                            pass
                        try:
                            client = _connect()
                        except Exception:
                            pass
                    time.sleep(interval)
                    # Keep the summary fresh so a kill mid-run still leaves a
                    # readable side-car.
                    if (time.monotonic() - last_summary_write) > 5:
                        _flush_summary()
                        last_summary_write = time.monotonic()
                    continue
                fh.write(sample.to_jsonl() + "\n")
                summary.n_samples += 1
                summary.distinct_setpoints.add(sample.setpoint)
                if sample.alarm:
                    summary.alarms_triggered += 1
                if sample.safety:
                    summary.safety_tripped = True
                if sample.trigger_fired > summary.trigger_fired_max:
                    summary.trigger_fired_max = sample.trigger_fired
                # Latch first_ts_ns on first nonzero observation, independent of
                # whether this sample is also a new max. This is correct even if
                # the counter resets mid-run (e.g. 0→5→0→3): we capture the
                # earliest moment any firing occurred.
                if sample.trigger_fired > 0 and summary.trigger_fired_first_ts_ns == 0:
                    summary.trigger_fired_first_ts_ns = sample.ts_ns
                if sample.trigger_seq_fired > summary.trigger_seq_fired_max:
                    summary.trigger_seq_fired_max = sample.trigger_seq_fired
                if sample.trigger_seq_fired > 0 and summary.trigger_seq_fired_first_ts_ns == 0:
                    summary.trigger_seq_fired_first_ts_ns = sample.ts_ns
                if sample.trigger_env_fired > summary.trigger_env_fired_max:
                    summary.trigger_env_fired_max = sample.trigger_env_fired
                if sample.trigger_env_fired > 0 and summary.trigger_env_fired_first_ts_ns == 0:
                    summary.trigger_env_fired_first_ts_ns = sample.ts_ns
                if (time.monotonic() - start) >= warmup_sec:
                    summary.n_after_warmup += 1
                    dev = abs(sample.level - sample.setpoint)
                    summary.sum_sq_deviation += dev * dev
                    if dev > summary.max_abs_deviation:
                        summary.max_abs_deviation = dev
                if (time.monotonic() - last_summary_write) > 5:
                    _flush_summary()
                    last_summary_write = time.monotonic()
                time.sleep(max(0.0, interval - (time.monotonic() - loop_start)))
    finally:
        try:
            client.close()
        except Exception:
            pass
        _flush_summary()

    return summary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="plc")
    p.add_argument("--port", type=int, default=502)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--interval", type=float, default=0.1)
    p.add_argument("--duration", type=float, default=300.0)
    p.add_argument("--warmup", type=float, default=10.0)
    args = p.parse_args()
    run(args.host, args.port, args.out, args.interval, args.duration, args.warmup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
