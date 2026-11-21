# The NEXUS Telegram bot image (ADR-040 step 1). Build context: the repository root, because the
# image carries three packages that live elsewhere in the repository (one copy of each, taken at
# build time):
#   libs/nexus-client/src/nexus_client     the Incident model, approval pinning, Kubernetes access
#   nexusctl/src/nexusctl/render           the renderer (standard library only; ADR-040 decision 11)
#   libs/nexus-revert/nexus_revert         C8, the revert pull request (ADR-041; PyYAML and the renderer)
# and the system git, which C8 runs (no recommended packages; the base's ca-certificates serve https).
# Runs as UID 10001; its only writes are C8's scratch clones under /tmp, an emptyDir, so the
# Deployment sets runAsNonRoot and readOnlyRootFilesystem (restricted Pod Security). No port: the
# bot is outbound only (decision 2).
# Base pinned by index digest (python:3.12-slim, 2026-10-02), as the operator image.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS builder
WORKDIR /build
COPY bot/requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --prefix=/install -r requirements.txt

FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
COPY --from=builder /install /usr/local
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 nexus-bot \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nexus-bot
WORKDIR /app
COPY libs/nexus-client/src/nexus_client nexus_client/
COPY nexusctl/src/nexusctl/__init__.py nexusctl/
COPY nexusctl/src/nexusctl/render nexusctl/render/
COPY libs/nexus-revert/nexus_revert nexus_revert/
COPY bot/nexus_bot nexus_bot/
USER 10001:10001
ENTRYPOINT ["python", "-m", "nexus_bot.main"]
