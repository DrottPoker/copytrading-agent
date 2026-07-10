#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vps.yml}"
BACKUP_DIR="${BACKUP_DIR:-backups/postgres}"
BACKUP_CONFIG_FILE="${BACKUP_CONFIG_FILE:-backend/config/backup.env}"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Run this script from the repository root or set COMPOSE_FILE." >&2
  exit 1
fi
if [ ! -f "$BACKUP_CONFIG_FILE" ]; then
  echo "Backup config was not found at $BACKUP_CONFIG_FILE." >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$BACKUP_CONFIG_FILE"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:?BACKUP_RETENTION_DAYS must be configured}"

mkdir -p "$BACKUP_DIR"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_DIR}/copyagent-postgres-${timestamp}.dump"

docker compose -f "$COMPOSE_FILE" exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$target"

if [ ! -s "$target" ]; then
  rm -f "$target"
  echo "Backup failed because the output file was empty." >&2
  exit 1
fi

find "$BACKUP_DIR" -type f -name 'copyagent-postgres-*.dump' -mtime +"$RETENTION_DAYS" -delete

echo "$target"
