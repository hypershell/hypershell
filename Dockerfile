FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

LABEL version="2.7.0"
LABEL authors="glentner@purdue.edu"
LABEL org.opencontainers.image.source="https://github.com/hypershell/hypershell"
LABEL org.opencontainers.image.description="HyperShell Base Image"
LABEL org.opencontainers.image.licenses="Apache-2.0"

RUN DEBIAN_FRONTEND=noninteractive \
    apt-get -yqq update && apt-get -yqq upgrade && \
    apt-get -yqq install build-essential postgresql libpq-dev && \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 1001 --system hypershell && \
    adduser --no-create-home --shell /bin/false --disabled-password --uid 1001 --system --group hypershell

RUN mkdir -p /var/lib/hypershell /var/log/hypershell

ENV UV_CACHE_DIR=/opt/uv-cache \
    UV_LINK_MODE=hardlink \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/hypershell \
    HYPERSHELL_LOGGING_LEVEL=TRACE \
    HYPERSHELL_LOGGING_STYLE=SYSTEM

WORKDIR /app
COPY . .
RUN /bin/rm -rf .venv .git
RUN uv sync --frozen --all-packages --python 3.13

USER hypershell
ENTRYPOINT ["/opt/hypershell/bin/hs", "server"]
CMD ["--forever"]
