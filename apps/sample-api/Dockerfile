# Base pinned by index digest (python:3.12-alpine, the same one the operator image uses; Dependabot's
# docker ecosystem proposes bumps). Alpine (musl) instead of Debian slim: far fewer OS packages to
# patch (TASKS.md M2-2 base-images item). Every wheel in requirements.txt has a musllinux build
# with its hash, and --only-binary makes pip refuse to compile anything.
# Build stage
FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8 AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes --prefix=/install -r requirements.txt

# Production stage
FROM python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8
# UID/GID 1000, what base/deployment.yaml runs the pod as (Debian gave 999; Alpine has a group there).
RUN addgroup -S -g 1000 nexus && adduser -S -D -H -u 1000 -G nexus -s /sbin/nologin nexus
WORKDIR /app
COPY --from=builder /install /usr/local
COPY main.py .
RUN chown -R nexus:nexus /app
USER nexus
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
