"""Device availability metric.

Polls Docker containers in the troia-ics compose stack, tracks restarts and
uptime percentage over a run window. Outputs JSONL per container, plus a
summary at run end.

Usage:
    python -m metrics.availability \\
        --containers troia-plc troia-hmi \\
        --out avaliacao/<run>/availability.jsonl \\
        --interval 1.0 \\
        --duration 300
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker  # type: ignore[import-not-found]


@dataclass(slots=True)
class ContainerSample:
    ts_ns: int
    name: str
    state: str         # "running", "exited", "restarting", "dead", ...
    restart_count: int
    pid: int

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "ts_ns": self.ts_ns,
                "name": self.name,
                "state": self.state,
                "restart_count": self.restart_count,
                "pid": self.pid,
            }
        )


@dataclass(slots=True)
class ContainerSummary:
    name: str
    n_samples: int = 0
    n_running: int = 0
    restart_count_start: int = 0
    restart_count_end: int = 0
    pids_seen: set[int] = field(default_factory=set)

    @property
    def uptime_pct(self) -> float:
        return (self.n_running / self.n_samples * 100.0) if self.n_samples else 0.0

    @property
    def restarts_during_run(self) -> int:
        # Docker's RestartCount only counts restarts driven by the restart
        # policy, not manual `docker restart` calls. We supplement it with
        # the count of distinct PIDs minus 1, which captures *every* time the
        # main process inside the container was replaced, including the
        # mutate-scenario's manual restart.
        policy = max(0, self.restart_count_end - self.restart_count_start)
        manual = max(0, len(self.pids_seen) - 1)
        return policy + manual

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n_samples": self.n_samples,
            "uptime_pct": round(self.uptime_pct, 3),
            "restarts_during_run": self.restarts_during_run,
            "distinct_pids": len(self.pids_seen),
        }


def sample_container(client: "docker.DockerClient", name: str) -> ContainerSample | None:
    try:
        c = client.containers.get(name)
    except docker.errors.NotFound:
        return None
    c.reload()
    state = c.attrs["State"]
    return ContainerSample(
        ts_ns=time.time_ns(),
        name=name,
        state=state.get("Status", "unknown"),
        restart_count=int(c.attrs.get("RestartCount", 0)),
        pid=int(state.get("Pid", 0)),
    )


def run(
    container_names: list[str],
    out_path: Path,
    interval: float,
    duration: float,
) -> dict[str, ContainerSummary]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # docker.from_env() trips a "Not supported URL scheme http+docker" bug
    # on docker-py 7.x against requests 2.31+ inside containers. Construct
    # the client explicitly against the bind-mounted Docker socket.
    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    client = docker.DockerClient(base_url=docker_host)

    summaries = {name: ContainerSummary(name=name) for name in container_names}
    initialised: set[str] = set()
    stop = False

    def _stop(_sig, _frm):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    deadline = time.monotonic() + duration if duration > 0 else float("inf")
    with out_path.open("a", encoding="utf-8") as fh:
        while not stop and time.monotonic() < deadline:
            loop_start = time.monotonic()
            for name in container_names:
                sample = sample_container(client, name)
                if sample is None:
                    continue
                fh.write(sample.to_jsonl() + "\n")
                fh.flush()
                s = summaries[name]
                if name not in initialised:
                    s.restart_count_start = sample.restart_count
                    initialised.add(name)
                s.restart_count_end = sample.restart_count
                s.n_samples += 1
                if sample.state == "running":
                    s.n_running += 1
                if sample.pid > 0:
                    s.pids_seen.add(sample.pid)
            time.sleep(max(0.0, interval - (time.monotonic() - loop_start)))

    # Summary side-car file.
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps({name: s.to_dict() for name, s in summaries.items()}, indent=2)
    )
    return summaries


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--containers", nargs="+", required=True)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--duration", type=float, default=300.0, help="0 = run until SIGTERM")
    args = p.parse_args()
    run(args.containers, args.out, args.interval, args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
