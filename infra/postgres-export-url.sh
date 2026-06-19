#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vps.yml}"
BACKUP_DIR="${BACKUP_DIR:-backups/postgres}"

if [ -z "${SOURCE_DATABASE_URL:-}" ]; then
  echo "Set SOURCE_DATABASE_URL to the source Postgres connection URL." >&2
  exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Run this script from the repository root or set COMPOSE_FILE." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_DIR}/external-postgres-${timestamp}.dump"

docker compose -f "$COMPOSE_FILE" run --rm --no-deps -e SOURCE_DATABASE_URL postgres sh -c \
  'pg_dump "$SOURCE_DATABASE_URL" --format=custom --no-owner --no-privileges' \
  > "$target"

if [ ! -s "$target" ]; then
  rm -f "$target"
  echo "Export failed because the output file was empty." >&2
  exit 1
fi

echo "$target"
