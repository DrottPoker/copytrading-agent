#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vps.yml}"

if [ "${CONFIRM_RESTORE:-}" != "yes" ]; then
  echo "Set CONFIRM_RESTORE=yes to restore. Stop app services before restoring." >&2
  exit 1
fi

if [ "$#" -ne 1 ]; then
  echo "Usage: CONFIRM_RESTORE=yes $0 backups/postgres/file.dump" >&2
  exit 1
fi

backup_file="$1"

if [ ! -f "$backup_file" ]; then
  echo "Backup file not found: $backup_file" >&2
  exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Run this script from the repository root or set COMPOSE_FILE." >&2
  exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' \
  < "$backup_file"

echo "Restore completed from $backup_file"
