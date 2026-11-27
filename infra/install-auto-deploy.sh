#!/usr/bin/env bash
# Install or remove the systemd timer that runs infra/auto-deploy-vps.sh.
#
# Run from the repository root as the deployment user (not root); it uses sudo:
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

if [ "${1:-}" = "--remove" ]; then
  sudo systemctl disable --now "$UNIT.timer" 2>/dev/null || true
  sudo rm -f "$SERVICE_PATH" "$TIMER_PATH"
  sudo systemctl daemon-reload
  echo "Removed $UNIT. Deploy manually with bash infra/deploy-vps.sh."
  exit 0
fi
[ -z "${1:-}" ] || fail "Unknown option: $1"

if [ ! -f docker-compose.vps.yml ] || [ ! -f infra/auto-deploy-vps.sh ]; then
  fail "Run this script from the repository root."
fi
[ -f .env ] || fail "The .env file is missing. Finish the first install in docs/deployment.md."
[ "$(id -u)" -ne 0 ] || fail "Run this as the deployment user, not as root."
id -nG | tr ' ' '\n' | grep -qx docker \
  || fail "$(id -un) is not in the docker group. See the first install steps."
for tool in git curl python3 flock systemctl; do
  command -v "$tool" >/dev/null || fail "$tool is required."
done

repo_dir="$(pwd -P)"
user="$(id -un)"
bash_path="$(command -v bash)"

sudo tee "$SERVICE_PATH" >/dev/null <<EOF
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

sudo tee "$TIMER_PATH" >/dev/null <<EOF
[Unit]
Description=Check for copytrading-agent updates every $INTERVAL

[Timer]
# Counts from when the timer starts: at boot, and right after this install.
OnActiveSec=2min
OnUnitInactiveSec=$INTERVAL

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT.timer"
echo "Installed $UNIT for $user in $repo_dir, checking every $INTERVAL."
echo "Follow it with: journalctl -u $UNIT.service -f"
