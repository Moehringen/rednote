FROM golang:1.26-bookworm AS xhs-builder

ARG XHS_VERSION=v0.2.0
RUN GOBIN=/out go install github.com/tamnd/xiaohongshu-cli/cmd/xhs@${XHS_VERSION}

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Singapore \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fluxbox novnc websockify x11vnc xauth xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-browser.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-browser.txt \
    && mkdir -p /ms-playwright \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright
COPY --from=xhs-builder /out/xhs /usr/local/bin/xhs
COPY --chown=10001:10001 app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --gid 10001 --home /home/app app \
    && mkdir -p /app/data/test_latest /app/data/xhs-config /app/data/xhs-cache /app/data/cookies \
    && chown -R 10001:10001 /app /home/app
RUN mkdir -p /tmp/.X11-unix \
    && chown root:root /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix
ENV HOME=/home/app
USER 10001:10001
VOLUME ["/app/data"]
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
