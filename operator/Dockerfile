# The NEXUS Operator image (M1b-8; spec §4, §19). Runs as UID 10001 with no writes at runtime, so
# the Deployment can set runAsNonRoot and readOnlyRootFilesystem (restricted Pod Security).
# Base pinned by index digest (python:3.12-slim, 2026-10-02).
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --prefix=/install -r requirements.txt

FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
COPY --from=builder /install /usr/local
WORKDIR /app
COPY nexus_operator/ nexus_operator/
USER 10001:10001
EXPOSE 8080
# Liveness only (Kopf's probe endpoint); metrics arrive in M2.
ENTRYPOINT ["kopf", "run", "--standalone", "--namespace", "nexus-system", \
            "--liveness=http://0.0.0.0:8080/healthz", "-m", "nexus_operator.main"]
