# The NEXUS Operator image (M1b-8; spec §4, §19). Runs as UID 10001 with no writes at runtime, so
# the Deployment can set runAsNonRoot and readOnlyRootFilesystem (restricted Pod Security).
# Base pinned by index digest (python:3.12-slim, 2026-10-02).
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --prefix=/install -r requirements.txt

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
COPY --from=builder /install /usr/local
# A passwd entry for UID 10001: Kopf's getpass.getuser() raised KeyError without one (#100).
# With it the Deployment needs no USER env; operator/tests/unit/test_runtime_user.py guards both.
RUN groupadd --gid 10001 nexus-operator \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nexus-operator
WORKDIR /app
COPY nexus_operator/ nexus_operator/
USER 10001:10001
EXPOSE 8080
# Liveness only (Kopf's probe endpoint); metrics arrive in M2.
ENTRYPOINT ["kopf", "run", "--standalone", "--namespace", "nexus-system", \
            "--liveness=http://0.0.0.0:8080/healthz", "-m", "nexus_operator.main"]
