# The Reasoner image (spec §16, §19). Build context: the repository root, because the image carries
# files that live elsewhere in the repository (one copy of each, taken at build time):
#   operator/nexus_operator/{catalogue,proposal,rules}.py   the Action Catalogue, proposal/v1, R1
#   (redact.py too, in the index stage only: operator_lib imports it)
#   experiments/harness/{bm25,rag,operator_lib}.py          the BM25 retriever and the index build
#   runbooks/                                                the corpus; the index is built from it here
# Standard library only: no pip install, no dependency to hash. Multi-arch (amd64, arm64).
# Base pinned by index digest (python:3.12-slim, 2026-10-02), as the operator image.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS index
WORKDIR /build
COPY operator/nexus_operator/__init__.py operator/nexus_operator/catalogue.py operator/nexus_operator/proposal.py operator/nexus_operator/rules.py operator/nexus_operator/redact.py operator/nexus_operator/
COPY experiments/harness/__init__.py experiments/harness/bm25.py experiments/harness/rag.py experiments/harness/operator_lib.py experiments/harness/
COPY runbooks runbooks
# Fails the build if the runbook lint fails (index runs the static rules first).
RUN python -m experiments.harness.rag index --out /build/index runbooks

FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
RUN groupadd --gid 10001 nexus-reasoner \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nexus-reasoner
WORKDIR /app
COPY operator/nexus_operator/__init__.py operator/nexus_operator/catalogue.py operator/nexus_operator/proposal.py operator/nexus_operator/rules.py nexus_operator/
COPY experiments/harness/__init__.py experiments/harness/bm25.py experiments/harness/
COPY reasoner/nexus_reasoner nexus_reasoner
COPY --from=index /build/index /app/index
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "nexus_reasoner"]
