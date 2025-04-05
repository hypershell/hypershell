FROM python:3.13-slim

LABEL version="2.6.6"
LABEL authors="glentner@purdue.edu"
LABEL org.opencontainers.image.source="https://github.com/hypershell/hypershell"
LABEL org.opencontainers.image.description="HyperShell Base Image"
LABEL org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update && \
    apt-get upgrade --yes && \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --gid 1001 --system app && \
    adduser --no-create-home --shell /bin/false --disabled-password --uid 1001 --system --group app

RUN mkdir -p /var/lib/hypershell

WORKDIR /app
COPY . .
RUN pip install .

ENV HYPERSHELL_LOGGING_LEVEL=TRACE \
    HYPERSHELL_LOGGING_STYLE=SYSTEM

USER app
ENTRYPOINT ["hs", "server"]
CMD ["--forever"]
