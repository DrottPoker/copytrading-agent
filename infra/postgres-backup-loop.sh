#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"

mkdir -p "$BACKUP_DIR"

while true; do
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  target="${BACKUP_DIR}/copyagent-postgres-${timestamp}.dump"

  if PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    -h "$POSTGRES_HOST" \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --format=custom \
    --no-owner \
    --no-privileges \
    > "$target"; then
    if [ -s "$target" ]; then
      find "$BACKUP_DIR" -type f -name 'copyagent-postgres-*.dump' -mtime +"$BACKUP_RETENTION_DAYS" -delete
      echo "Backup completed: $target"
    else
      rm -f "$target"
      echo "Backup failed because the output file was empty." >&2
    fi
  else
    rm -f "$target"
    echo "Backup failed." >&2
  fi

  sleep "$BACKUP_INTERVAL_SECONDS"
done
