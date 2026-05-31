# Dedicated traffic-capture sidecar. Runs in the PLC's network namespace
# (set via docker-compose `network_mode: "container:troia-plc"`), which lets
# tcpdump see every frame that enters or leaves the PLC's veth interface —
# including the attacker -> plc and hmi -> plc Modbus/TCP traffic that would
# be invisible to a container with its own isolated namespace.
#
# Separating capture from the `monitor` container preserves monitor's role as
# an independent observer of availability and integrity (those channels do
# NOT share the PLC's namespace).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        tcpdump \
        ca-certificates \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/troia
CMD ["sleep", "infinity"]
