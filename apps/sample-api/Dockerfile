# Base pinned by index digest (python:3.12-slim, the same one the operator image uses; Dependabot's
# docker ecosystem proposes bumps).
# Build stage
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.txt

# Production stage
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
RUN groupadd -r nexus && useradd -r -g nexus -s /bin/false nexus
WORKDIR /app
COPY --from=builder /install /usr/local
COPY main.py .
RUN chown -R nexus:nexus /app
USER nexus
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
