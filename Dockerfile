FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# NOTE: no static version label — the version is single-sourced from pyproject.toml, and CI
# (docker/metadata-action) stamps org.opencontainers.image.version from the release tag.
LABEL authors="glentner@purdue.edu"
LABEL org.opencontainers.image.source="https://github.com/hypershell/hypershell"
LABEL org.opencontainers.image.description="HyperShell Base Image"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# All dependencies resolve to wheels on Python 3.14 (see uv.lock), so no build toolchain is needed.
# libpq5 supplies the runtime shared library for the pure-python psycopg used by the dev group.
RUN DEBIAN_FRONTEND=noninteractive \
    apt-get -yqq update && \
    apt-get -yqq upgrade && \
    apt-get -yqq install libpq5 && \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 1001 --system hypershell && \
    adduser --no-create-home --shell /bin/false --disabled-password --uid 1001 --system --group hypershell

RUN mkdir -p /var/lib/hypershell /var/log/hypershell

ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_LINK_MODE=hardlink \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/hypershell \
    HYPERSHELL_LOGGING_LEVEL=TRACE \
    HYPERSHELL_LOGGING_STYLE=SYSTEM \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY . .
RUN uv sync --frozen --all-packages --python 3.14

USER hypershell
ENTRYPOINT ["/opt/hypershell/bin/hs", "server"]
CMD ["--forever"]
