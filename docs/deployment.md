# VPS Deployment

This guide runs the full paper trading stack on a Linux VPS with Docker Compose.

## What Runs

`docker-compose.vps.yml` starts:

- `backend`: FastAPI API on the internal Docker network.
- `worker`: discovery, pool import, scoring, pruning, realtime monitoring, and paper copy.
- `frontend`: Next.js dashboard on the internal Docker network.
- `redis`: local Redis with append-only persistence.
- `caddy`: public reverse proxy on ports 80 and 443.

The VPS compose file does not publish backend or frontend ports directly. Only
Caddy is exposed publicly.

## Requirements

- A Linux VPS with Docker and the Docker Compose plugin.
- A domain or subdomain pointing to the VPS public IP.
- A Postgres database. Neon works with the existing `DATABASE_URL` settings.
- Ports 80 and 443 open in the VPS firewall and cloud firewall.

## First Install

Install Docker:

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

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
DATABASE_URL=postgresql+asyncpg://user:password@host-pooler.region.aws.neon.tech/dbname?ssl=require
DATABASE_URL_DIRECT=postgresql://user:password@host.region.aws.neon.tech/dbname?sslmode=require
REDIS_URL=redis://redis:6379/0

DASHBOARD_AUTH_USERNAME=admin
DASHBOARD_AUTH_PASSWORD=replace-with-a-strong-password
DASHBOARD_AUTH_ENABLED=true
DASHBOARD_DOMAIN=dashboard.example.com
SERVER_API_BASE_URL=http://backend:8000
```

For paper trading, Hyperliquid private key settings can remain empty. Do not
enable live trading on the VPS until the paper trading system has been validated.

Build the images:

```bash
docker compose -f docker-compose.vps.yml build
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
docker compose -f docker-compose.vps.yml logs -f backend worker frontend caddy
```

Open:

```text
https://dashboard.example.com
```

## Updates

Pull changes, rebuild, migrate, and restart:

```bash
git pull
docker compose -f docker-compose.vps.yml build
docker compose -f docker-compose.vps.yml run --rm backend python -m alembic upgrade head
docker compose -f docker-compose.vps.yml up -d
```

Paper trading state is stored in Postgres, not in the worker container. After the
worker restarts it reloads open paper positions and runs paper-copy recovery for
fills imported while the stack was down.

## Operational Commands

Check service status:

```bash
docker compose -f docker-compose.vps.yml ps
```

Show recent logs:

```bash
docker compose -f docker-compose.vps.yml logs --tail=200 backend worker
```

Restart the worker after config changes:

```bash
docker compose -f docker-compose.vps.yml restart worker
```

Stop the stack:

```bash
docker compose -f docker-compose.vps.yml down
```

## Notes

- The default VPS compose file expects an external Postgres database.
- Redis data is stored in the `redis_data` Docker volume.
- Caddy certificates are stored in `caddy_data` and `caddy_config` volumes.
- Backend routes are protected by dashboard Basic Auth except `/health` and
  `/ready`.
- The dashboard calls the backend through the Next.js server-side proxy, so the
  backend does not need a public domain.
