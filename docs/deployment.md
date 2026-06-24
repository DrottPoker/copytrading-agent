# VPS Deployment

This guide runs the full paper trading stack on a Linux VPS with Docker Compose
and local Postgres.

## What Runs

`docker-compose.vps.yml` starts:

- `postgres`: local Postgres 18 with persistent Docker volume storage.
- `redis`: local Redis with append-only persistence.
- `backend`: FastAPI API on the internal Docker network.
- `trading-worker`: realtime monitoring, paper copy, and paper-copy recovery.
- `maintenance-worker`: discovery, pool import, scoring, pruning, and database maintenance.
- `frontend`: Next.js dashboard on the internal Docker network.
- `caddy`: public reverse proxy on ports 80 and 443.

The VPS compose file does not publish backend, frontend, Postgres, or Redis
ports directly. Only Caddy is exposed publicly.

## Requirements

- A Linux VPS with Docker and the Docker Compose plugin.
- Recommended minimum for local Postgres: 2 vCPU, 4 GB RAM, and SSD storage.
- A domain or subdomain pointing to the VPS public IP, or `DASHBOARD_DOMAIN=:80`
  for HTTP by IP only.
- Ports 80 and 443 open in the VPS firewall and cloud firewall when using a
  domain. Port 80 is enough for IP-only HTTP.

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
DASHBOARD_AUTH_PASSWORD=replace-with-a-strong-password
DASHBOARD_AUTH_ENABLED=true
DASHBOARD_DOMAIN=dashboard.example.com
SERVER_API_BASE_URL=http://backend:8000
BACKUP_STATUS_ENABLED=true
BACKUP_INTERVAL_SECONDS=86400
BACKUP_RETENTION_DAYS=7
```

Use a URL-safe Postgres password because Docker Compose builds database URLs
from `POSTGRES_*`. This is safe and simple:

```bash
openssl rand -hex 24
```

For paper trading, Hyperliquid private key settings can remain empty. Do not
enable live trading on the VPS until the paper trading system has been validated.

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
