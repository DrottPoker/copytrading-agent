#!/usr/bin/env bash
# Update a VPS deployment: back up, pull, build, migrate, restart, and verify.
#
# Usage from the repository root:
#   bash infra/deploy-vps.sh          # asks before building when config templates changed
#   bash infra/deploy-vps.sh --yes    # never asks
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vps.yml}"
APP_SERVICES=(backend trading-worker maintenance-worker frontend caddy)
ASSUME_YES=false
if [ "${1:-}" = "--yes" ]; then
  ASSUME_YES=true
fi

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

fail() {
  echo "$*" >&2
  exit 1
}

[ -f "$COMPOSE_FILE" ] || fail "Run this script from the repository root or set COMPOSE_FILE."
[ -f .env ] || fail "The .env file is missing. Use the first install steps in docs/deployment.md."

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no >&2
  fail "Tracked files have local changes. Save them before updating; never reset them away."
fi

echo "Backing up the database before the update..."
backup="$(COMPOSE_FILE="$COMPOSE_FILE" bash infra/postgres-backup-local.sh)"
env_copy=".env.before-update-$(date -u +%Y%m%dT%H%M%SZ)"
cp .env "$env_copy"
chmod 600 "$env_copy"
echo "Database backup: $backup"
echo "Environment copy: $env_copy"

previous_commit="$(git rev-parse HEAD)"
git pull --ff-only
current_commit="$(git rev-parse HEAD)"
if [ "$previous_commit" = "$current_commit" ]; then
  echo "Already at $(git rev-parse --short HEAD); rebuilding and restarting anyway."
else
  echo "Updating $(git rev-parse --short "$previous_commit") -> $(git rev-parse --short "$current_commit")"
  changed_config="$(git diff --name-only "$previous_commit" "$current_commit" -- \
    .env.example backend/config frontend/config)"
  if [ -n "$changed_config" ]; then
    echo "These configuration templates changed in the update:"
    while IFS= read -r path; do
      echo "  $path"
    done <<<"$changed_config"
    echo "Compare .env with .env.example and review the config files before continuing."
    if [ "$ASSUME_YES" != true ]; then
      read -r -p "Continue with build and restart? [y/N] " answer
      case "$answer" in
        y | Y | yes | YES) ;;
        *) fail "Stopped before building. Run the script again when ready." ;;
      esac
    fi
  fi
fi

compose config --quiet
compose build
compose up -d postgres redis

# Stop every database reader and writer so migrations cannot deadlock with them.
compose stop "${APP_SERVICES[@]}"
if ! compose up -d --remove-orphans; then
  echo "Start failed. When the migrate service failed, the application stays stopped." >&2
  echo "Inspect it with: docker compose -f $COMPOSE_FILE logs migrate" >&2
  echo "A PostgreSQL deadlock is rolled back; run this script again." >&2
  fail "Restore point if needed: $backup"
fi

current_revision="$(compose run --rm --no-deps -T migrate python -m alembic current 2>/dev/null \
  | awk 'NF {print $1}' | tail -n 1)"
head_revision="$(compose run --rm --no-deps -T migrate python -m alembic heads 2>/dev/null \
  | awk 'NF {print $1}' | tail -n 1)"
if [ -z "$head_revision" ] || [ "$current_revision" != "$head_revision" ]; then
  fail "Database revision '${current_revision:-unknown}' does not match head '${head_revision:-unknown}'."
fi
echo "Database is at Alembic head $head_revision."

echo "Waiting for the backend health check..."
for _ in $(seq 1 24); do
  if compose exec -T backend python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read()" \
    >/dev/null 2>&1; then
    compose ps
    echo "Update complete."
    exit 0
  fi
  sleep 5
done

compose ps >&2
fail "The backend did not become healthy within two minutes. Check: docker compose -f $COMPOSE_FILE logs backend"
