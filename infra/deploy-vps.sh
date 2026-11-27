#!/usr/bin/env bash
# Update a VPS deployment: back up, pull, build, migrate, restart, and verify.
#
# Usage from the repository root:
#   bash infra/deploy-vps.sh                  # asks before building when config templates changed
#   bash infra/deploy-vps.sh --yes            # never asks
#   bash infra/deploy-vps.sh --commit <sha>   # fast-forwards to a fetched commit instead of pulling
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.vps.yml}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-.deploy-state}"
APP_SERVICES=(backend trading-worker maintenance-worker frontend caddy)
# Environment copies to keep; older ones are deleted because each holds secrets.
ENV_COPIES_TO_KEEP=20
ASSUME_YES=false
TARGET_COMMIT=""

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

fail() {
  echo "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes) ASSUME_YES=true ;;
    --commit)
      [ "$#" -ge 2 ] || fail "--commit needs a commit hash."
      TARGET_COMMIT="$2"
      shift
      ;;
    *) fail "Unknown option: $1" ;;
  esac
  shift
done

[ -f "$COMPOSE_FILE" ] || fail "Run this script from the repository root or set COMPOSE_FILE."
[ -f .env ] || fail "The .env file is missing. Use the first install steps in docs/deployment.md."

# One deployment at a time. infra/auto-deploy-vps.sh takes this lock itself and passes it on.
if [ "${DEPLOY_LOCK_HELD:-}" != 1 ]; then
  mkdir -p "$DEPLOY_STATE_DIR"
  exec 9>"$DEPLOY_STATE_DIR/lock"
  flock -n 9 || fail "Another deployment is running. Wait for it to finish."
fi

dashboard_domain="$(sed -n 's/^DASHBOARD_DOMAIN=//p' .env | tail -n 1)"
case "$dashboard_domain" in
  :* | http://*)
    echo "Warning: DASHBOARD_DOMAIN=$dashboard_domain serves the dashboard over plain HTTP." >&2
    echo "The dashboard password is sent unencrypted. Use a DNS name for automatic HTTPS" >&2
    echo "before live trading; see docs/deployment.md." >&2
    ;;
esac

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no >&2
  fail "Tracked files have local changes. Save them before updating; never reset them away."
fi

echo "Backing up the database before the update..."
backup="$(COMPOSE_FILE="$COMPOSE_FILE" bash infra/postgres-backup-local.sh)"
env_copy=".env.before-update-$(date -u +%Y%m%dT%H%M%SZ)"
cp .env "$env_copy"
chmod 600 "$env_copy"
# Glob results sort by name, and the timestamp format puts the newest last.
shopt -s nullglob
env_copies=(.env.before-update-*)
shopt -u nullglob
if [ "${#env_copies[@]}" -gt "$ENV_COPIES_TO_KEEP" ]; then
  rm -f -- "${env_copies[@]:0:${#env_copies[@]}-ENV_COPIES_TO_KEEP}"
fi
echo "Database backup: $backup"
echo "Environment copy: $env_copy"

previous_commit="$(git rev-parse HEAD)"
if [ -n "$TARGET_COMMIT" ]; then
  git merge --ff-only --quiet "$TARGET_COMMIT"
else
  git pull --ff-only
fi
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

# Commit date (UTC) and short hash, shown in the dashboard sidebar and on the System page.
APP_VERSION="$(TZ=UTC git log -1 --abbrev=7 --date=format-local:%Y.%m.%d --format=%cd-%h HEAD)"
export APP_VERSION
echo "Version: $APP_VERSION"

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
    # A successful deployment releases any commit infra/auto-deploy-vps.sh held.
    rm -f "$DEPLOY_STATE_DIR/held-commit"
    echo "Update complete."
    exit 0
  fi
  sleep 5
done

compose ps >&2
fail "The backend did not become healthy within two minutes. Check: docker compose -f $COMPOSE_FILE logs backend"
