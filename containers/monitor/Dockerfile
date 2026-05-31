FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3-pip \
        python3.12-venv \
        tcpdump \
        net-tools \
        iproute2 \
        iputils-ping \
        procps \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/troia

# Build context is the repo root.
COPY containers/monitor/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Package layout: monitor (multi-channel Isolation Forest detector) + metrics
# (per-channel collectors). The container is idle by default; metric scripts
# and the detector are invoked by scripts/run_matrix.sh via docker exec.
COPY monitor/ ./monitor/
COPY metrics/ ./metrics/
COPY analysis/ ./analysis/

ENV PYTHONPATH=/opt/troia
CMD ["sleep", "infinity"]
