#!/usr/bin/env bash
# Deploy the newest commit on the tracked branch once its CI run has passed.
# A systemd timer runs it every few minutes; see infra/install-auto-deploy.sh
# and docs/deployment.md.
#
# Usage from the repository root:
#   bash infra/auto-deploy-vps.sh            # deploy when a new commit passed CI
#   bash infra/auto-deploy-vps.sh --status   # show deployed, latest, and held commits
#   bash infra/auto-deploy-vps.sh --retry    # clear a held commit and try it again
set -euo pipefail

BRANCH="${AUTO_DEPLOY_BRANCH:-main}"
REMOTE="${AUTO_DEPLOY_REMOTE:-origin}"
CI_WORKFLOW="${AUTO_DEPLOY_CI_WORKFLOW:-ci.yml}"
GITHUB_API_URL="${GITHUB_API_URL:-https://api.github.com}"
DEPLOY_SCRIPT="${DEPLOY_SCRIPT:-infra/deploy-vps.sh}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-.deploy-state}"
HELD_FILE="$DEPLOY_STATE_DIR/held-commit"
LAST_LOG_FILE="$DEPLOY_STATE_DIR/last-message"
TOKEN_FILE="$DEPLOY_STATE_DIR/github-token"
# Same paths infra/deploy-vps.sh lists; a change here may need .env edits first.
CONFIG_PATHS=(.env.example backend/config frontend/config)

fail() {
  echo "$*" >&2
  exit 1
}

# Print a message only when it differs from the previous one, so a run every few
# minutes does not repeat the same line.
log_once() {
  if [ ! -f "$LAST_LOG_FILE" ] || [ "$(cat "$LAST_LOG_FILE")" != "$*" ]; then
    echo "$*"
    printf '%s\n' "$*" >"$LAST_LOG_FILE"
  fi
}

held_message() {
  echo "Commit $(short "$1") is held ($2). See docs/deployment.md."
}

# Hold a commit until a newer one arrives, a deployment succeeds, or --retry.
# Prints the reason once; later runs stay quiet about the same hold.
hold() {
  log_once "$3"
  printf '%s %s\n' "$1" "$2" >"$HELD_FILE"
  held_message "$1" "$2" >"$LAST_LOG_FILE"
}

short() {
  git rev-parse --short "$1"
}

github_repository() {
  if [ -n "${AUTO_DEPLOY_REPOSITORY:-}" ]; then
    echo "$AUTO_DEPLOY_REPOSITORY"
    return
  fi
  local url repo
  url="$(git remote get-url "$REMOTE")"
  case "$url" in
    *github.com[:/]*) repo="${url#*github.com[:/]}" ;;
    *) fail "Remote $REMOTE ($url) is not on GitHub. Set AUTO_DEPLOY_REPOSITORY=owner/name." ;;
  esac
  repo="${repo%/}"
  echo "${repo%.git}"
}

# Prints success, pending, missing, or the conclusion of a finished CI run
# (failure, cancelled, timed_out, ...) for the newest push run of a commit.
ci_result() {
  local repo url
  repo="$(github_repository)"
  url="$GITHUB_API_URL/repos/$repo/actions/workflows/$CI_WORKFLOW/runs?head_sha=$1&event=push&per_page=1"
  local headers=("Accept: application/vnd.github+json" "X-GitHub-Api-Version: 2022-11-28")
  if [ -s "$TOKEN_FILE" ]; then
    headers+=("Authorization: Bearer $(tr -d '[:space:]' <"$TOKEN_FILE")")
  fi
  # Headers go through stdin so a token never shows up in the process list.
  printf '%s\n' "${headers[@]}" \
    | curl -fsS --max-time 30 -H @- "$url" \
    | python3 -c '
import json, sys
runs = json.load(sys.stdin).get("workflow_runs", [])
if not runs:
    print("missing")
elif runs[0]["status"] != "completed":
    print("pending")
else:
    print(runs[0]["conclusion"] or "unknown")
'
}

fetch_branch() {
  git fetch --quiet "$REMOTE" "$BRANCH"
  git rev-parse "$REMOTE/$BRANCH"
}

show_status() {
  local latest
  latest="$(fetch_branch)"
  echo "Branch:   $(git symbolic-ref --short -q HEAD || echo detached)"
  echo "Deployed: $(git log -1 --format='%h %cs %s' HEAD)"
  echo "Latest:   $(git log -1 --format='%h %cs %s' "$latest") ($REMOTE/$BRANCH)"
  if [ "$latest" != "$(git rev-parse HEAD)" ]; then
    echo "CI:       $(ci_result "$latest")"
  fi
  if [ -f "$HELD_FILE" ]; then
    read -r held_sha held_reason <"$HELD_FILE"
    echo "Held:     $(short "$held_sha") ($held_reason)"
  else
    echo "Held:     none"
  fi
  if [ -f "$LAST_LOG_FILE" ]; then
    echo "Last:     $(cat "$LAST_LOG_FILE")"
  fi
}

MODE=deploy
case "${1:-}" in
  "") ;;
  --status) MODE=status ;;
  --retry) MODE=retry ;;
  *) fail "Unknown option: $1" ;;
esac

[ -f docker-compose.vps.yml ] || fail "Run this script from the repository root."
for tool in git curl python3 flock; do
  command -v "$tool" >/dev/null || fail "$tool is required."
done
mkdir -p "$DEPLOY_STATE_DIR"

if [ "$MODE" = status ]; then
  show_status
  exit 0
fi

exec 9>"$DEPLOY_STATE_DIR/lock"
if ! flock -n 9; then
  [ "$MODE" = deploy ] && exit 0
  fail "Another deployment is running. Wait for it to finish."
fi

held_sha=""
held_reason=""
if [ -f "$HELD_FILE" ]; then
  read -r held_sha held_reason <"$HELD_FILE"
fi
retry_sha=""
if [ "$MODE" = retry ]; then
  rm -f "$HELD_FILE" "$LAST_LOG_FILE"
  retry_sha="$held_sha"
  held_sha=""
fi

current_branch="$(git symbolic-ref --short -q HEAD || true)"
if [ "$current_branch" != "$BRANCH" ]; then
  log_once "The checkout is on '${current_branch:-a detached HEAD}', not $BRANCH. Not deploying."
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  log_once "Tracked files have local changes. Not deploying until they are saved elsewhere."
  exit 1
fi

if ! fetch_error="$(git fetch --quiet "$REMOTE" "$BRANCH" 2>&1)"; then
  log_once "Could not fetch $REMOTE/$BRANCH. Trying again later. ${fetch_error//$'\n'/ }"
  exit 0
fi
latest="$(git rev-parse "$REMOTE/$BRANCH")"
deployed="$(git rev-parse HEAD)"

if [ -n "$held_sha" ]; then
  # A failed deployment may already have moved HEAD, so this check comes before
  # the up-to-date check.
  if [ "$held_sha" = "$latest" ]; then
    log_once "$(held_message "$latest" "$held_reason")"
    exit 0
  fi
  # A newer commit replaces the held one.
  rm -f "$HELD_FILE"
fi

# A retried failed deployment runs again even when HEAD already moved.
if [ "$latest" = "$deployed" ] && [ "$retry_sha" != "$latest" ]; then
  [ "$MODE" = retry ] && echo "Already at $(short "$deployed"). Nothing to deploy."
  exit 0
fi

if ! git merge-base --is-ancestor "$deployed" "$latest"; then
  hold "$latest" not-fast-forward \
    "Commit $(short "$latest") does not fast-forward from $(short "$deployed"). Not deploying."
  exit 1
fi

if ! ci="$(ci_result "$latest")"; then
  log_once "Could not read the CI result for $(short "$latest") from GitHub. Trying again later."
  exit 0
fi
case "$ci" in
  success) ;;
  pending | missing)
    log_once "Waiting for CI on $(short "$latest")."
    exit 0
    ;;
  *)
    # Not held: a rerun that passes deploys the commit on the next check.
    log_once "CI on $(short "$latest") ended with '$ci'. Not deploying."
    exit 0
    ;;
esac

changed_config="$(git diff --name-only "$deployed" "$latest" -- "${CONFIG_PATHS[@]}")"
if [ -n "$changed_config" ]; then
  echo "Configuration templates changed between $(short "$deployed") and $(short "$latest"):"
  while IFS= read -r path; do
    echo "  $path"
  done <<<"$changed_config"
  hold "$latest" config-changed \
    "Commit $(short "$latest") is held (config-changed). Review .env and deploy with bash infra/deploy-vps.sh."
  exit 0
fi

echo "Deploying $(short "$deployed") -> $(short "$latest")."
if DEPLOY_LOCK_HELD=1 DEPLOY_STATE_DIR="$DEPLOY_STATE_DIR" bash "$DEPLOY_SCRIPT" --yes --commit "$latest"; then
  log_once "Deployed $(short "$latest")."
  exit 0
fi
hold "$latest" deploy-failed \
  "Deploying $(short "$latest") failed. Fix the cause, then run bash infra/auto-deploy-vps.sh --retry."
exit 1
