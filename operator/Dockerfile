# The NEXUS Operator image (M1b-8; spec §4, §19). Runs as UID 10001 with no writes at runtime, so
# the Deployment can set runAsNonRoot and readOnlyRootFilesystem (restricted Pod Security).
# Base pinned by index digest (python:3.12-alpine, 2026-11-02; was python:3.12-slim, 2026-10-02).
# Alpine (musl): far fewer OS packages to patch (TASKS.md M2-2 base-images item).
FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8 AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --prefix=/install -r requirements.txt

FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
COPY --from=builder /install /usr/local
# A passwd entry for the runtime UID: Kopf names its peering identity with getpass.getuser(), which
# falls back to pwd.getpwuid() and raised KeyError at the #100 live gate. The Deployment's USER env
# is the workaround until this image is deployed (TASKS.md Later).
RUN addgroup -S -g 10001 nexus-operator \
 && adduser -S -D -H -u 10001 -G nexus-operator -s /sbin/nologin nexus-operator
WORKDIR /app
COPY nexus_operator/ nexus_operator/
USER 10001:10001
EXPOSE 8080
# Liveness only (Kopf's probe endpoint); metrics arrive in M2.
ENTRYPOINT ["kopf", "run", "--standalone", "--namespace", "nexus-system", \
            "--liveness=http://0.0.0.0:8080/healthz", "-m", "nexus_operator.main"]
