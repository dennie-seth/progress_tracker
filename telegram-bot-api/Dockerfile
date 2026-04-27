# ==========================================
# Stage 1: builder
# ==========================================
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    make \
    git \
    zlib1g-dev \
    libssl-dev \
    gperf \
    cmake \
    g++ \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --recursive https://github.com/tdlib/telegram-bot-api.git /usr/src/telegram-bot-api \
    && cd /usr/src/telegram-bot-api \
    && mkdir build \
    && cd build \
    && cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=/usr/local .. \
    && cmake --build . --target install -j2

# ==========================================
# Stage 2: runtime
# ==========================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    zlib1g \
    libssl3 \
    ca-certificates \
    tzdata \
    tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

# Wrapper script handles cascading SIGINT -> SIGTERM -> SIGQUIT shutdown so
# the C++ daemon unwinds cleanly when `docker compose stop` arrives.
COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh

RUN useradd -u 1000 -m -d /var/lib/telegram-bot-api -s /bin/bash telegram-bot-api ; \
    chmod +x /usr/local/bin/entrypoint.sh

USER telegram-bot-api
EXPOSE 8081

ENTRYPOINT ["/usr/bin/tini", "--"]

CMD ["/usr/local/bin/entrypoint.sh"]
