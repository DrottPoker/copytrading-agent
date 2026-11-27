#!/usr/bin/env bash
# Install or remove the systemd timer that runs infra/auto-deploy-vps.sh.
#
# Run from the repository root, as root or as a user with sudo. The timer runs
# as the user that owns the checkout, so deployments never leave files owned by
# another user behind:
#   bash infra/install-auto-deploy.sh            # install and start the timer
#   bash infra/install-auto-deploy.sh --remove   # stop and remove the timer
set -euo pipefail

UNIT="copytrading-auto-deploy"
INTERVAL="${AUTO_DEPLOY_INTERVAL:-5min}"
SERVICE_PATH="/etc/systemd/system/$UNIT.service"
TIMER_PATH="/etc/systemd/system/$UNIT.timer"

fail() {
  echo "$*" >&2
  exit 1
}

as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [ "${1:-}" = "--remove" ]; then
  as_root systemctl disable --now "$UNIT.timer" 2>/dev/null || true
  as_root rm -f "$SERVICE_PATH" "$TIMER_PATH"
  as_root systemctl daemon-reload
  echo "Removed $UNIT. Deploy manually with bash infra/deploy-vps.sh."
  exit 0
fi
[ -z "${1:-}" ] || fail "Unknown option: $1"

if [ ! -f docker-compose.vps.yml ] || [ ! -f infra/auto-deploy-vps.sh ]; then
  fail "Run this script from the repository root."
fi
[ -f .env ] || fail "The .env file is missing. Finish the first install in docs/deployment.md."
for tool in git curl python3 flock systemctl stat; do
  command -v "$tool" >/dev/null || fail "$tool is required."
done

repo_dir="$(pwd -P)"
user="$(stat -c %U "$repo_dir")"
if [ "$user" != root ]; then
  id -nG "$user" | tr ' ' '\n' | grep -qx docker \
    || fail "$user owns $repo_dir but is not in the docker group. See the first install steps."
fi
bash_path="$(command -v bash)"

as_root tee "$SERVICE_PATH" >/dev/null <<EOF
[Unit]
Description=Deploy new copytrading-agent commits that passed CI
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
User=$user
WorkingDirectory=$repo_dir
ExecStart=$bash_path infra/auto-deploy-vps.sh
# Image builds can take a while on a small server.
TimeoutStartSec=45min
EOF

as_root tee "$TIMER_PATH" >/dev/null <<EOF
[Unit]
Description=Check for copytrading-agent updates every $INTERVAL

[Timer]
# Counts from when the timer starts: at boot, and right after this install.
OnActiveSec=2min
OnUnitInactiveSec=$INTERVAL

[Install]
WantedBy=timers.target
EOF

as_root systemctl daemon-reload
as_root systemctl enable --now "$UNIT.timer"
echo "Installed $UNIT for $user in $repo_dir, checking every $INTERVAL."
echo "Follow it with: journalctl -u $UNIT.service -f"
