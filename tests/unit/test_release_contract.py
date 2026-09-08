"""Regression tests for statements the article makes about the released artefact.

These pin claims a reader can check by cloning the repository. Each one was wrong at
some point during the Access-2026-32283 audit:

  * three of the five reproduction targets named in Section VII did not exist;
  * the smoke test required a host /proc mount that the compose file deliberately
    does not grant, so it could never pass;
  * the parser's PDU, malformed and function-code definitions back Table 7.
"""

from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

import pytest

from metrics.modbus_parser import parse_mbap_pdu, parse_pcap

REPO = Path(__file__).resolve().parents[2]


# --- reproduction targets named in the article ------------------------------------

@pytest.mark.parametrize("target", [
    "smoke-test",
    "reproduce-table-3-lite",
    "reproduce-binary-separation-class1",
    "reproduce-binary-separation-all",
    "reproduce-full",
])
def test_makefile_target_named_in_the_article_resolves(target):
    r = subprocess.run(["make", "-n", target], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, f"`make {target}` is cited in Section VII but does not resolve"


@pytest.mark.parametrize("alias", [
    "reproduce-table-2", "reproduce-killer-demo", "reproduce-killer-demo-class1",
])
def test_legacy_target_names_still_resolve(alias):
    """The pre-2026-09 names are kept so older instructions do not break."""
    r = subprocess.run(["make", "-n", alias], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0


# --- privilege model --------------------------------------------------------------

def test_monitor_is_not_granted_host_pid_namespace_or_proc():
    """Section IV-A states that no container holds host-level inspection privileges.

    The smoke test used to require exactly that privilege and therefore always failed.
    """
    compose = (REPO / "docker-compose.yml").read_text()
    monitor = compose[compose.index("\n  monitor:"):]
    monitor = monitor[:monitor.index("\n  ", 1) if "\n  " in monitor[1:] else len(monitor)]
    assert "pid: host" not in compose
    assert "/proc:/host/proc" not in compose
    assert "/proc:" not in compose


def test_smoke_test_asserts_the_absence_of_host_proc():
    """Absence of host /proc must be a PASS condition, never a FAIL."""
    src = (REPO / "scripts/smoke_test.sh").read_text()
    assert "privilege model holds" in src, "the smoke test must assert the privilege model"
    assert "did not find an 'openplc' process under /host/proc" not in src, \
        "the removed step required a privilege the architecture does not grant"


def test_smoke_test_waits_for_modbus_rather_than_skipping_once():
    src = (REPO / "scripts/smoke_test.sh").read_text()
    assert "MODBUS_UP" in src, "step 5 must wait for :502 instead of probing once"


# --- Table 7 parser definitions ---------------------------------------------------

def _mbap(txid=1, proto=0, unit=1, pdu=b"\x03\x00\x00\x00\x01"):
    length = len(pdu) + 1
    return struct.pack(">HHHB", txid, proto, length, unit) + pdu


def test_wellformed_pdu_is_not_counted_as_malformed():
    fc, length, malformed = parse_mbap_pdu(_mbap())
    assert (fc, malformed) == (0x03, False)
    assert length == 6


def test_short_payload_is_malformed():
    assert parse_mbap_pdu(b"\x00\x01\x00") is None, "under 8 bytes cannot carry an MBAP header"


def test_nonzero_protocol_identifier_is_malformed():
    _fc, _l, malformed = parse_mbap_pdu(_mbap(proto=7))
    assert malformed is True


def test_declared_length_longer_than_payload_is_malformed():
    p = bytearray(_mbap())
    struct.pack_into(">H", p, 4, 400)          # declare far more than is present
    _fc, _l, malformed = parse_mbap_pdu(bytes(p))
    assert malformed is True


def test_exception_response_keeps_its_base_function_code():
    fc, _l, _m = parse_mbap_pdu(_mbap(pdu=b"\x83\x02"))
    assert fc == 0x83 and (fc & 0x7F) == 0x03, "0x80 marks an exception, 0x03 is the base code"


def _write_pcap(path: Path, packets):
    """Minimal Ethernet/IPv4/TCP pcap so the parser's own reader is exercised."""
    with path.open("wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for ts_us, payload in packets:
            tcp = struct.pack(">HHIIBBHHH", 40000, 502, 0, 0, 5 << 4, 0x18, 8192, 0, 0) + payload
            ip_len = 20 + len(tcp)
            ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, ip_len, 0, 0, 64, 6, 0,
                             bytes([10, 0, 0, 1]), bytes([10, 0, 0, 2]))
            frame = b"\x00" * 12 + b"\x08\x00" + ip + tcp
            f.write(struct.pack("<IIII", ts_us // 1_000_000, ts_us % 1_000_000,
                                len(frame), len(frame)) + frame)


def test_parser_counts_pdus_malformed_and_function_codes(tmp_path: Path):
    """One end-to-end check of the three quantities Table 7 is built from."""
    bad = bytearray(_mbap())
    struct.pack_into(">H", bad, 4, 400)
    pcap = tmp_path / "t.pcap"
    _write_pcap(pcap, [
        (0,       _mbap(pdu=b"\x03\x00\x00\x00\x01")),
        (100_000, _mbap(pdu=b"\x06\x00\x00\x00\x2a")),
        (200_000, bytes(bad)),
        (300_000, b"\x00\x01\x00"),
    ])
    summary, _ = parse_pcap(pcap)
    assert summary.total_pdus == 4
    assert summary.malformed == 2, "one over-declared length and one truncated payload"
    assert summary.fc_histogram == {0x03: 2, 0x06: 1}, "the truncated payload never reaches the histogram"
    assert summary.unknown_fc == 0 and summary.exceptions == 0
