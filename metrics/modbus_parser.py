"""Modbus/TCP protocol-anomaly metric.

Reads a pcap file (captured during a run by `tcpdump -i br0 -w out.pcap`) and
counts, both globally and per fixed-width time window:
  - total Modbus/TCP PDUs
  - malformed PDUs (length mismatch, truncated MBAP header)
  - unknown/reserved function codes (>0x10 outside the standard set)
  - exception responses (function code bit 0x80 set)

Two outputs:
  - `modbus.summary.json`        — run-aggregate (one row per run)
  - `modbus_windows.jsonl`       — one record per time window (matches
    `monitor.detector` windowing, so the detector can compute a per-window
    malformed rate instead of uniformly distributing the aggregate)

Usage:
    python -m metrics.modbus_parser \\
        --pcap avaliacao/<run>/capture.pcap \\
        --out avaliacao/<run>/modbus.summary.json \\
        --windows-out avaliacao/<run>/modbus_windows.jsonl \\
        --window-sec 5
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scapy.all import PcapReader, TCP, IP  # type: ignore[import-not-found]


STANDARD_FCS = {
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0B, 0x0C,
    0x0F, 0x10, 0x11, 0x14, 0x15, 0x16, 0x17, 0x18, 0x2B,
}
MODBUS_TCP_PORT = 502


@dataclass(slots=True)
class ModbusSummary:
    total_pdus: int = 0
    malformed: int = 0
    unknown_fc: int = 0
    exceptions: int = 0
    fc_histogram: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convert int keys to str for JSON.
        d["fc_histogram"] = {f"0x{k:02x}": v for k, v in self.fc_histogram.items()}
        return d


def parse_mbap_pdu(payload: bytes) -> tuple[int, int, bool] | None:
    """Return (function_code, declared_length, is_malformed) or None if too short.

    MBAP header: txid(2) + protoid(2) + length(2) + unit_id(1). Then PDU starts
    with function_code(1). Declared length includes unit_id + PDU.
    """
    if len(payload) < 8:
        return None  # too short for header
    _txid, proto, length, _uid = struct.unpack(">HHHB", payload[:7])
    fc = payload[7]
    # Protocol identifier MUST be 0 for Modbus/TCP.
    if proto != 0:
        return fc, length, True
    # Declared length should equal len(payload) - 6 (txid+proto+length itself).
    expected_total = length + 6
    is_malformed = len(payload) < expected_total or length < 2
    return fc, length, is_malformed


@dataclass(slots=True)
class WindowCounts:
    window_start_ns: int
    window_end_ns: int
    total_pdus: int = 0
    malformed: int = 0
    unknown_fc: int = 0
    exceptions: int = 0

    @property
    def malformed_rate(self) -> float:
        return self.malformed / self.total_pdus if self.total_pdus else 0.0

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "window_start_ns": self.window_start_ns,
                "window_end_ns": self.window_end_ns,
                "total_pdus": self.total_pdus,
                "malformed": self.malformed,
                "unknown_fc": self.unknown_fc,
                "exceptions": self.exceptions,
                "malformed_rate": round(self.malformed_rate, 6),
            }
        )


def _packet_ts_ns(pkt) -> int:
    """Extract packet timestamp in nanoseconds."""
    return int(float(pkt.time) * 1_000_000_000)


def parse_pcap(
    pcap_path: Path,
    window_sec: float | None = None,
) -> tuple[ModbusSummary, list[WindowCounts]]:
    """Parse the pcap once, producing both the run-aggregate summary and, if
    window_sec is given, the list of per-window counts in chronological order.
    """
    summary = ModbusSummary()
    windows: list[WindowCounts] = []
    window_ns = int(window_sec * 1_000_000_000) if window_sec else 0

    first_ts_ns: int | None = None
    current: WindowCounts | None = None

    with PcapReader(str(pcap_path)) as reader:
        for pkt in reader:
            if not (pkt.haslayer(TCP) and pkt.haslayer(IP)):
                continue
            tcp = pkt[TCP]
            if tcp.dport != MODBUS_TCP_PORT and tcp.sport != MODBUS_TCP_PORT:
                continue
            payload = bytes(tcp.payload)
            if not payload:
                continue

            ts_ns = _packet_ts_ns(pkt)
            if window_sec and window_ns:
                if first_ts_ns is None:
                    first_ts_ns = ts_ns
                # Allocate window if needed.
                if current is None or ts_ns >= current.window_end_ns:
                    bucket = (ts_ns - first_ts_ns) // window_ns
                    w_start = first_ts_ns + bucket * window_ns
                    current = WindowCounts(window_start_ns=w_start, window_end_ns=w_start + window_ns)
                    windows.append(current)

            parsed = parse_mbap_pdu(payload)
            summary.total_pdus += 1
            if current is not None:
                current.total_pdus += 1
            if parsed is None:
                summary.malformed += 1
                if current is not None:
                    current.malformed += 1
                continue
            fc, _length, is_malformed = parsed
            base_fc = fc & 0x7F
            summary.fc_histogram[base_fc] = summary.fc_histogram.get(base_fc, 0) + 1
            if is_malformed:
                summary.malformed += 1
                if current is not None:
                    current.malformed += 1
            if base_fc not in STANDARD_FCS:
                summary.unknown_fc += 1
                if current is not None:
                    current.unknown_fc += 1
            if fc & 0x80:
                summary.exceptions += 1
                if current is not None:
                    current.exceptions += 1

    return summary, windows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pcap", required=True, type=Path, nargs="+",
                   help="One or more pcap files to parse in order. Multiple "
                        "files are useful for the mutate scenario where the "
                        "PLC restart fragments the capture across two parts.")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--windows-out", type=Path, default=None,
                   help="Optional: write per-window counts as JSONL")
    p.add_argument("--window-sec", type=float, default=5.0)
    args = p.parse_args()

    window_sec = args.window_sec if args.windows_out else None

    # Parse each pcap in sequence and merge. Windowing is timestamp-based,
    # so a single bucket can span across files (windows_by_start_ns).
    merged_summary = ModbusSummary()
    merged_windows: dict[tuple[int, int], WindowCounts] = {}
    for pcap_path in args.pcap:
        s, ws = parse_pcap(pcap_path, window_sec=window_sec)
        merged_summary.total_pdus += s.total_pdus
        merged_summary.malformed += s.malformed
        merged_summary.unknown_fc += s.unknown_fc
        merged_summary.exceptions += s.exceptions
        for fc, n in s.fc_histogram.items():
            merged_summary.fc_histogram[fc] = merged_summary.fc_histogram.get(fc, 0) + n
        for w in ws:
            key = (w.window_start_ns, w.window_end_ns)
            existing = merged_windows.get(key)
            if existing is None:
                merged_windows[key] = w
            else:
                existing.total_pdus += w.total_pdus
                existing.malformed += w.malformed
                existing.unknown_fc += w.unknown_fc
                existing.exceptions += w.exceptions

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged_summary.to_dict(), indent=2))

    if args.windows_out:
        args.windows_out.parent.mkdir(parents=True, exist_ok=True)
        sorted_windows = [merged_windows[k] for k in sorted(merged_windows.keys())]
        with args.windows_out.open("w", encoding="utf-8") as fh:
            for w in sorted_windows:
                fh.write(w.to_jsonl() + "\n")
        print(f"wrote {len(sorted_windows)} per-window records -> {args.windows_out}")

    print(json.dumps(merged_summary.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
