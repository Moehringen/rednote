#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${DEPLOY_BASE_DIR:-/opt/rednote_to_x}"
SOURCE_DIR="$BASE_DIR/source"
ENV_FILE="$BASE_DIR/.env"
XURL_DIR="${XURL_AUTH_DIR:-$BASE_DIR/xurl}"
AUTH_FILE="$XURL_DIR/auth.yml"

if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE" >&2
  exit 1
fi
if [ ! -s "$AUTH_FILE" ]; then
  echo "missing $AUTH_FILE; transfer the xurl auth store first" >&2
  exit 1
fi

sudo install -d -m 700 -o 10001 -g 10001 "$XURL_DIR"
sudo chown 10001:10001 "$AUTH_FILE"
sudo chmod 600 "$AUTH_FILE"

sudo sed -i -E '/^(X_AUTH_MODE|X_POST_API_VERSION|X_TOKEN_SOURCE|XURL_APP|XURL_PATH|X_USER_ACCESS_TOKEN|X_API_KEY|X_API_SECRET|X_ACCESS_TOKEN|X_ACCESS_TOKEN_SECRET)=/d' "$ENV_FILE"
printf '%s\n' \
  'X_AUTH_MODE="oauth2"' \
  'X_POST_API_VERSION="v2"' \
  'X_TOKEN_SOURCE="xurl"' \
  'XURL_APP="girlinrednote"' \
  'XURL_PATH="/usr/local/bin/xurl"' \
  'X_USER_ACCESS_TOKEN=""' \
  | sudo tee -a "$ENV_FILE" >/dev/null
sudo chmod 600 "$ENV_FILE"

sudo env \
  HOST_DATA_DIR="$BASE_DIR/data" \
  XURL_AUTH_DIR="$XURL_DIR" \
  DEPLOY_ENV_FILE="$ENV_FILE" \
  docker compose \
    --env-file "$ENV_FILE" \
    -f "$SOURCE_DIR/docker-compose.yml" \
    up -d --remove-orphans

sudo docker exec rednote-to-x xurl --app girlinrednote /2/users/me >/dev/null
sudo docker inspect -f 'status={{.State.Status}} health={{.State.Health.Status}}' rednote-to-x
