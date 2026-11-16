#!/usr/bin/env bash
set -euo pipefail

# This script is streamed over gcloud compute ssh by GitHub Actions.
# It never replaces the persistent .env or data directories.
BASE_DIR="${DEPLOY_BASE_DIR:-/opt/rednote_to_x}"
SOURCE_DIR="$BASE_DIR/source"
REPOSITORY_URL="${DEPLOY_REPOSITORY_URL:-https://github.com/Moehringen/rednote.git}"
DEPLOY_SHA="${1:?deployment commit SHA is required}"

sudo install -d -m 755 "$BASE_DIR" "$BASE_DIR/data"
if [ ! -f "$BASE_DIR/.env" ]; then
  echo "missing $BASE_DIR/.env; create it before enabling automatic deployment" >&2
  exit 1
fi

if [ ! -d "$SOURCE_DIR/.git" ]; then
  if [ -e "$SOURCE_DIR" ]; then
    echo "$SOURCE_DIR exists but is not a Git checkout; refusing to overwrite it" >&2
    exit 1
  fi
  sudo git clone --depth 1 "$REPOSITORY_URL" "$SOURCE_DIR"
else
  sudo git -C "$SOURCE_DIR" fetch --depth 1 origin main
fi
sudo git -C "$SOURCE_DIR" fetch --depth 1 origin "$DEPLOY_SHA"
sudo git -C "$SOURCE_DIR" checkout --force "$DEPLOY_SHA"

# The container runs as UID/GID 10001 and owns the durable browser profile,
# downloaded media, SQLite database, and optional CLI cache.
sudo chown -R 10001:10001 "$BASE_DIR/data"
sudo chmod 700 "$BASE_DIR/data"

sudo env \
  HOST_DATA_DIR="$BASE_DIR/data" \
  DEPLOY_ENV_FILE="$BASE_DIR/.env" \
  docker compose \
    --env-file "$BASE_DIR/.env" \
    -f "$SOURCE_DIR/docker-compose.yml" \
    up -d --build --remove-orphans

sudo env \
  HOST_DATA_DIR="$BASE_DIR/data" \
  DEPLOY_ENV_FILE="$BASE_DIR/.env" \
  docker compose \
    --env-file "$BASE_DIR/.env" \
    -f "$SOURCE_DIR/docker-compose.yml" \
    ps
