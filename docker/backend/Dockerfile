# Backend (api) image for docker/compose.yaml, profile `app` (plan §5). No PII. NOT BUILT YET.
#
# Before building, two owner decisions are needed (CLAUDE.md: ask before installing anything not listed):
#  1. Approval of the extra downloads below: the uv binary image and Node.js for the OpenRedaction sidecar.
#     Pin both by digest at that point (the Debian base below is already pinned and present locally).
#  2. A reviewed "container mode" for the API: it binds 127.0.0.1 and refuses non-loopback clients, so
#     behind Docker's port publishing every request would be refused. Relaxing that is a security change.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

# uv (manages the pinned CPython 3.12 and installs exactly backend/uv.lock); pin by digest when approved
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv

ENV UV_PYTHON_PREFERENCE=only-managed \
    UV_COMPILE_BYTECODE=1 \
    OMP_THREAD_LIMIT=1 ORT_NUM_THREADS=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/
COPY backend/src backend/src
COPY config config
COPY schemas schemas
COPY experiments experiments
COPY fixtures fixtures
COPY sidecars sidecars
RUN cd backend && uv python install 3.12 && uv sync --frozen --no-dev

# Confidential folders are mounted at runtime (REDACTOR_DATA_ROOT), never copied into the image.
ENV REDACTOR_DATA_ROOT=/confidential
EXPOSE 8765
WORKDIR /app/backend
CMD ["uv", "run", "--frozen", "redactor", "api", "serve", "--port", "8765"]
