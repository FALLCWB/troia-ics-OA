FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=America/Sao_Paulo

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        build-essential \
        pkg-config \
        bison \
        flex \
        autoconf \
        automake \
        libtool \
        make \
        nodejs \
        python3 \
        python3-pip \
        sqlite3 \
        cmake \
        unzip \
        ca-certificates \
        sudo \
        net-tools \
        iproute2 \
        iputils-ping \
        procps \
        curl \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Build OpenPLC v3 from upstream source. The commit is pinned to a specific
# hash for reproducibility; override at build time with --build-arg if needed.
# Last refresh: 2026-05-18. See VERSIONS.md.
ARG OPENPLC_COMMIT=b5d41356dab4aeadca0dd7ca64ba542f870b595d
RUN git clone https://github.com/thiagoralves/OpenPLC_v3.git /opt/openplc \
    && cd /opt/openplc \
    && git checkout ${OPENPLC_COMMIT}
WORKDIR /opt/openplc
RUN ./install.sh linux

# Tank PID program + bootstrap that auto-loads it via the OpenPLC web UI.
COPY programs/ /programs/
COPY bootstrap.sh /usr/local/bin/bootstrap.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/bootstrap.sh /usr/local/bin/entrypoint.sh

EXPOSE 8080 502 20000

WORKDIR /opt/openplc
CMD ["/usr/local/bin/entrypoint.sh"]
