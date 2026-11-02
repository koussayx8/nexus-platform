# Comparison images only: never pushed, signed or deployed (TASKS.md M2-2 base-images item).
# One Dockerfile builds the same app on different bases so Trivy counts are comparable. The
# runtime set is installed WITHOUT hashes (the deployed images use hashed, Debian-glibc wheels;
# the alpine and distroless variants need wheels the hash files do not list). Context: an app
# directory (apps/sample-api or operator).
ARG BUILDER=python:3.12-slim
ARG RUNTIME=python:3.12-slim

FROM ${BUILDER} AS builder
WORKDIR /build
COPY requirements.txt .
RUN sed -E 's/ *\\$//; /^ *--hash=/d' requirements.txt > plain.txt \
    && pip install --no-cache-dir --prefix=/install -r plain.txt

FROM ${RUNTIME}
ARG PYVER=3.12
COPY --from=builder /install /usr/local
ENV PYTHONPATH=/app:/usr/local/lib/python${PYVER}/site-packages
WORKDIR /app
COPY . /app
