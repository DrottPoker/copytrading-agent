# VPS Deployment

This guide runs the full paper trading stack on a Linux VPS with Docker Compose
and local Postgres.

## What Runs

`docker-compose.vps.yml` starts:

- `postgres`: local Postgres 18 with persistent Docker volume storage.
- `redis`: local Redis with append-only persistence.
- `backend`: FastAPI API on the internal Docker network.
- `trading-worker`: realtime monitoring, paper copy, live copy when enabled,
  copy recovery, and live reconciliation.
- `maintenance-worker`: discovery, pool import, scoring, pruning, and database maintenance.
- `frontend`: Next.js dashboard on the internal Docker network.
- `caddy`: public reverse proxy on ports 80 and 443.

The VPS compose file does not publish backend, frontend, Postgres, or Redis
ports directly. Only Caddy is exposed publicly.

## Requirements

- A Linux VPS with Docker and the Docker Compose plugin.
- Recommended minimum for local Postgres: 2 vCPU, 4 GB RAM, and SSD storage.
- A domain or subdomain pointing to the VPS public IP. Plain HTTP and IP-only
  dashboard deployments are not supported because Basic Auth must be protected
  by TLS.
- Ports 80 and 443 open in the VPS firewall and cloud firewall. Caddy uses the
  domain to provision TLS and redirect HTTP traffic to HTTPS.

## First Install

Install Docker:

```bash
sudo apt update
sudo apt install -y git docker.io
sudo systemctl enable --now docker
```

If your distro does not provide `docker compose`, install Docker from the
official Docker apt repository and install the Compose plugin from there.

Clone the repository:

```bash
git clone <repo-url>
cd copytrading-agent
```

Create the environment file:

```bash
cp .env.example .env
nano .env
```

Set at least these values:

```env
POSTGRES_DB=copyagent
POSTGRES_USER=copyagent
POSTGRES_PASSWORD=replace-with-openssl-rand-hex-24-output

REDIS_URL=redis://redis:6379/0

DASHBOARD_AUTH_USERNAME=admin
DASHBOARD_AUTH_PASSWORD=replace-with-a-unique-16-plus-character-password
DASHBOARD_AUTH_ENABLED=true
DASHBOARD_DOMAIN=dashboard.example.com
SERVER_API_BASE_URL=http://backend:8000
BACKUP_STATUS_ENABLED=true
BACKUP_INTERVAL_SECONDS=86400
BACKUP_RETENTION_DAYS=7
```

`DASHBOARD_DOMAIN` must be a DNS name that resolves to the VPS. Do not use
`:80`, a raw IP address, or another plain HTTP listener. The dashboard uses
Basic Auth, so it must only be exposed through Caddy with HTTPS.

Production validation requires a non-empty dashboard username and a unique
dashboard password of at least 16 characters. Invalid configuration errors
redact input values so secrets are not written to validation logs.

Use a URL-safe Postgres password because Docker Compose builds database URLs
from `POSTGRES_*`. This is safe and simple:

```bash
openssl rand -hex 24
```

For paper trading, Hyperliquid private key settings can remain empty. The
repository uses mainnet market data with live trading and live copy disabled. Do not
enable live trading on the VPS until the paper trading system has been validated.
Live trading requires explicit environment or JSON overrides for `enabled=true`,
`acknowledged=true`, a configured `HYPERLIQUID_PRIVATE_KEY`, and
`HYPERLIQUID_WALLET_ADDRESS`. Mainnet also requires
`mainnet_acknowledged=true`. Starting a mainnet account and submitting mainnet
entries require a non-empty `LIVE_TRADING_ALLOWED_COINS` list plus
`LIVE_TRADING_MAINNET_ARMING_TOKEN=ARM_MAINNET_LIVE_TRADING` and timezone-aware
`LIVE_TRADING_MAINNET_ARMED_AT` and `LIVE_TRADING_MAINNET_ARMED_UNTIL` values
whose window spans no more than 24 hours. Restart
the backend and trading worker after changing the arming window. When the window
expires, new mainnet exposure is blocked while reduce-only exits remain allowed.
The key is read from environment or config only and is not stored in Postgres.
Live reconciliation settings live in the same file
under `reconciliation`; the worker reads Hyperliquid order status, fills, and
clearinghouse state for enabled live accounts. It also recovers durable live
order outbox rows and unfinished close-all operations. Run Alembic migrations
before restarting the backend and workers so the dispatch and operation tables
and reconciliation run history exist. Partial reconciliation preserves the
last known value for failed exchange components and blocks new live entries
until a complete worker or manual reconciliation succeeds. Reduce-only exits
remain available. `account.capital_mode` defaults
to `unified`, which expects the Hyperliquid wallet to run Unified account mode
and reads trading capital from `spotClearinghouseState`. Set it to
`standard_per_dex` only when the wallet intentionally keeps separate default and
HIP-3 perp balances. Automatic live copy also requires
`copy_execution.enabled=true` in the same file. Use testnet and the
`POST /trading/testnet/orders` endpoint before any mainnet enablement.
Live copy shares source ranking, allocation, min-order, price drift, and
mid-price cache policy with paper copy through `backend/config/trading.json`.
Live order execution limits and account risk guardrails stay in
`backend/config/live_trading.json`.

Build the images:

```bash
docker compose -f docker-compose.vps.yml build
```

Start Postgres and Redis:

```bash
docker compose -f docker-compose.vps.yml up -d postgres redis
```

Run database migrations:

```bash
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
```

Start the stack:

```bash
docker compose -f docker-compose.vps.yml up -d
```

Follow logs:

```bash
docker compose -f docker-compose.vps.yml logs -f backend trading-worker maintenance-worker frontend caddy postgres postgres-backup
```

Check internal backend routing if dashboard API calls fail:

```bash
docker compose -f docker-compose.vps.yml ps
docker compose -f docker-compose.vps.yml exec frontend wget -qO- http://backend:8000/health
docker compose -f docker-compose.vps.yml logs --tail=100 backend frontend
```

Open:

```text
https://dashboard.example.com
```

## Moving From External Postgres

Use this flow when the current VPS already uses Supabase, Neon, or another
external Postgres database and you want to move the data into local VPS
Postgres.

Save the current environment before editing it:

```bash
cp .env .env.before-local-postgres
```

Pull the new code:

```bash
git pull
```

Edit `.env` and add local Postgres settings:

```bash
nano .env
```

Set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`. Keep the old
external URL available in `.env.before-local-postgres`.

Stop app services so the external database stops receiving writes:

```bash
docker compose -f docker-compose.vps.yml stop backend trading-worker maintenance-worker frontend caddy
```

Start local Postgres:

```bash
docker compose -f docker-compose.vps.yml up -d postgres
```

Export the old external database. Use the direct external URL, not the pooler
URL:

```bash
OLD_DATABASE_URL_DIRECT="$(grep '^DATABASE_URL_DIRECT=' .env.before-local-postgres | cut -d= -f2-)"
SOURCE_DATABASE_URL="$OLD_DATABASE_URL_DIRECT" bash infra/postgres-export-url.sh
```

The export container uses the same Postgres major version as local Postgres.
This matters because `pg_dump` must be the same major version or newer than the
source database server. If you previously started an empty local Postgres volume
with an older image before importing data, stop Postgres, remove only that empty
local volume, pull the updated compose file, and start Postgres again before
restoring.

Restore the newest exported dump into local Postgres:

```bash
LATEST_DUMP="$(ls -t backups/postgres/external-postgres-*.dump | head -n 1)"
CONFIRM_RESTORE=yes bash infra/postgres-restore-local.sh "$LATEST_DUMP"
```

Build, migrate, and start:

```bash
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml up -d --remove-orphans
```

Verify services:

```bash
docker compose -f docker-compose.vps.yml ps
docker compose -f docker-compose.vps.yml logs --tail=100 backend trading-worker maintenance-worker postgres postgres-backup
```

## Updates

Pull changes, rebuild, migrate, and restart:

```bash
git pull
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml up -d postgres redis
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml up -d --remove-orphans
```

Paper trading state is stored in local Postgres, not in the worker containers.
After the trading worker restarts it reloads open paper positions, replays
recent source fills, and checks source live perp state so paper positions can
close if the source exited while the stack was down.

## Phase 6 Safety Upgrade

Use this sequence once when an existing VPS first receives migration
`e5a1c7d9b3f2`. The migration creates the durable global live-entry control in
`paused` state and changes enabled live accounts to `exit_only`. It stops for
manual review if legacy account relationships or active live routes are
ambiguous.

Create and verify a pre-upgrade backup before building or migrating:

```bash
mkdir -p backups/postgres
docker compose -f docker-compose.vps.yml exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > backups/postgres/pre-phase6.dump
test -s backups/postgres/pre-phase6.dump
```

Build the Phase 6 images, run the migration, and restart the API, both worker
roles, the dashboard, and Caddy:

```bash
docker compose -f docker-compose.vps.yml build backend trading-worker maintenance-worker frontend
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml up -d backend trading-worker maintenance-worker frontend caddy
```

After the services are healthy:

1. Open Accounts in the dashboard.
2. Run and confirm a complete fresh reconciliation for each live account.
3. Review the effective risk limits shown in the Live Entry Safety panel.
4. Confirm that any account intended for new exposure is in the expected
   lifecycle state.
5. Explicitly resume global live entries with a recorded reason only if live
   execution is intended.
6. Start individual live accounts only after the global entry state is enabled.

Do not update `live_entry_safety_controls` directly in Postgres. Configuration
flags and service restarts do not resume the durable entry gate.

## Backups

Docker Compose runs a `postgres-backup` service by default. It waits for local
Postgres to become healthy, writes an immediate dump to `backups/postgres`, then
repeats every 24 hours. The default retention is 7 days.

Relevant settings:

```env
BACKUP_STATUS_ENABLED=true
BACKUP_INTERVAL_SECONDS=86400
BACKUP_RETENTION_DAYS=7
```

Show backup logs:

```bash
docker compose -f docker-compose.vps.yml logs --tail=100 postgres-backup
```

Create an immediate backup by restarting the backup worker. It writes one dump
at startup and then continues on the 24 hour interval:

```bash
docker compose -f docker-compose.vps.yml restart postgres-backup
```

By default, backups are written to `backups/postgres` and backup files older
than 7 days are deleted. Override retention in `.env`:

```env
BACKUP_RETENTION_DAYS=30
```

Restore a backup only while app services are stopped:

```bash
docker compose -f docker-compose.vps.yml stop backend trading-worker maintenance-worker frontend caddy
CONFIRM_RESTORE=yes bash infra/postgres-restore-local.sh backups/postgres/copyagent-postgres-YYYYMMDDTHHMMSSZ.dump
docker compose -f docker-compose.vps.yml up -d
```

## Operational Commands

Check service status:

```bash
docker compose -f docker-compose.vps.yml ps
```

Show recent logs:

```bash
docker compose -f docker-compose.vps.yml logs --tail=200 backend trading-worker maintenance-worker postgres postgres-backup
```

Restart workers after config changes:

```bash
docker compose -f docker-compose.vps.yml restart trading-worker maintenance-worker
```

Stop the stack:

```bash
docker compose -f docker-compose.vps.yml down
```

Do not use this unless you intentionally want to delete all local Docker volume
data:

```bash
docker compose -f docker-compose.vps.yml down -v
```

## Notes

- VPS Postgres data is stored in the `postgres_data` Docker volume.
- Set `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` before the first
  Postgres start. Changing them later does not rename or re-own an existing
  database volume.
- Redis data is stored in the `redis_data` Docker volume.
- Caddy certificates are stored in `caddy_data` and `caddy_config` volumes.
- The backend mounts `./backups/postgres` read-only so `/ops` can show latest
  backup status when `BACKUP_STATUS_ENABLED=true`.
- The `postgres-backup` service mounts `./backups/postgres` read-write and
  creates `copyagent-postgres-*.dump` files every 24 hours by default.
- Backend routes are protected by dashboard Basic Auth except `/health` and
  `/ready`. The dashboard itself also requires the same Basic Auth.
- The dashboard calls the backend through the Next.js server-side proxy, so the
  backend does not need a public domain.
- Keep at least a few GB of free disk space for Postgres WAL, autovacuum, and
  backups.
