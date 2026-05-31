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
        ca-certificates \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/troia

# Build context is the repo root.
COPY containers/attacker/requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Package source: attacker/ (modes) + metrics/ (shared helpers if any in future).
COPY attacker/ ./attacker/

ENV PYTHONPATH=/opt/troia
CMD ["sleep", "infinity"]
