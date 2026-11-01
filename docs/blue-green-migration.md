# Blue-green migration for claude.onnmed.com

Status: **planned, not started** (written 2026-11-01).

Goal: deploys with no downtime. Today `make deploy` rebuilds the live containers in place,
which takes about 36 s of downtime.

## Current setup (as of 2026-11-01)

| Item | Value |
|---|---|
| Server | `root@46.4.91.91` (key `~/.ssh/id_ed25519`), checkout `/root/claude-code-proxy`, remote = fork |
| TLS + ports 80/443 | Host nginx (shared with every other site on the box), certbot certs |
| nginx site | `/etc/nginx/sites-available/claude.onnmed.com.conf`: `/api` → `127.0.0.1:13080`, `/` → `127.0.0.1:13000` |
| Compose project | `claude-code-proxy`, files from `COMPOSE_FILE` in `.env`: `docker-compose.yaml:docker-compose.postgres.yml:docker-compose.nginx.local.yml` |
| Postgres | Container `claude-code-proxy-postgres`, network `claude-code-proxy_default`, `POSTGRES_HOST=postgres` |
| Port mapping | `docker-compose.nginx.local.yml` (untracked, server only) maps backend → `13080`, frontend → `13000` |

Why `scripts/blue-green.sh` can't run yet: it switches slots with Traefik router priorities, and this
server has no Traefik. nginx must keep 80/443 because other sites use them.

## Target

```
nginx :443 (TLS, unchanged) ──► Traefik 127.0.0.1:13090 ──► claude-code-proxy-blue | claude-code-proxy-green
                                                       Postgres stays in project claude-code-proxy
```

Traefik and Postgres live in the existing `claude-code-proxy` project. Each slot is its own project
(`claude-code-proxy-blue` / `-green`). It never starts `traefik` or `postgres`, because the script passes an
explicit service list.

What already works: `docker-compose.traefik.yml` has slot-aware labels, the backend sends
`X-Deployment-Slot` (`backend/app/main.py`), and the notification dispatcher uses
`FOR UPDATE SKIP LOCKED`, so two dispatchers during the overlap don't double-send.

## Phase 1: repo changes (about 20 min, no production impact)

1. **`docker-compose.traefik.yml`:** add to the `traefik` command so Traefik doesn't pick the backend's
   non-Traefik network IP (that causes 502s):
   ```yaml
   - --providers.docker.network=claude-code-proxy-traefik
   ```

2. **New `docker-compose.live.yml`** (overlay for slots: reach the shared DB and the shared Traefik network):
   ```yaml
   services:
     backend-init-db: { networks: [default, shared-db] }
     backend: { networks: [default, traefik, shared-db] }
     quota-refresher: { networks: [default, shared-db] }
     notification-dispatcher: { networks: [default, shared-db] }
     archive-retention: { networks: [default, shared-db] }
   networks:
     traefik: { external: true, name: claude-code-proxy-traefik }
     shared-db: { external: true, name: claude-code-proxy_default }
   ```

3. **`scripts/blue-green.sh`:** read these from `.env` too. The script already has `env_file_value`.
   - `compose_files="${BG_COMPOSE_FILES:-$(env_file_value BG_COMPOSE_FILES)}"` (keep the current default when both are empty)
   - `public_origin="${BG_URL:-$(env_file_value BG_URL)}"` (fall back to `$default_url`)
   - `drain_seconds="${BG_DRAIN_SECONDS:-$(env_file_value BG_DRAIN_SECONDS)}"` (fall back to `10`)

4. **`Makefile`:** make `deploy` refuse to run once blue-green is active:
   ```make
   deploy:
   	@test ! -f .blue-green-state || { echo "Blue-green is active: use make deploy-blue-green." >&2; exit 1; }
   ```

5. Verify locally. Both must succeed:
   ```bash
   BG_SLOT=blue BG_PRIORITY=1 BG_API_PRIORITY=2 docker compose -p claude-code-proxy-blue \
     -f docker-compose.yaml -f docker-compose.traefik.yml -f docker-compose.live.yml config --quiet
   BG_COMPOSE_FILES=docker-compose.yaml:docker-compose.traefik.yml:docker-compose.live.yml \
     BG_VALIDATE_ONLY=1 scripts/blue-green.sh
   ```

Commit, push to the fork, then `git pull` on the server.

## Phase 2: switchover on the server (about 30 min, no downtime)

Before starting, check: `DOMAIN=claude.onnmed.com` is in `.env` (the Traefik `Host()` rule uses it),
and nothing else reads `HTTP_PORT`/`HTTPS_PORT` (`grep -rn HTTP_PORT backend frontend`).

1. **Back up, then add to `.env`:**
   ```bash
   cd /root/claude-code-proxy && cp -p .env .env.bak-$(date +%Y%m%d-%H%M%S)
   ```
   ```env
   HTTP_PORT=127.0.0.1:13090
   HTTPS_PORT=127.0.0.1:13443
   TRAEFIK_ENTRYPOINT=web
   BG_URL=https://claude.onnmed.com
   BG_COMPOSE_FILES=docker-compose.yaml:docker-compose.traefik.yml:docker-compose.live.yml
   BG_DRAIN_SECONDS=600
   ```
   Then change `COMPOSE_FILE` to
   `docker-compose.yaml:docker-compose.postgres.yml:docker-compose.traefik.yml`
   (drop `nginx.local.yml`; slots get no host ports).

2. **Start Traefik only.** Nothing routes to it yet:
   ```bash
   docker compose up -d --no-deps traefik
   ```

3. **Start the blue slot at its final priority, and check it through Traefik:**
   ```bash
   BG_SLOT=blue BG_PRIORITY=101 BG_API_PRIORITY=102 docker compose -p claude-code-proxy-blue \
     -f docker-compose.yaml -f docker-compose.traefik.yml -f docker-compose.live.yml \
     up -d --build --wait --wait-timeout 180 \
     backend-init-db backend quota-refresher notification-dispatcher archive-retention frontend
   curl -sI -H 'Host: claude.onnmed.com' http://127.0.0.1:13090/api/health | grep -i x-deployment-slot   # blue
   curl -sI -H 'Host: claude.onnmed.com' http://127.0.0.1:13090/          | grep -i x-deployment-slot   # blue
   ```

4. **Switch nginx.** This is the only step that changes live traffic. A reload doesn't drop connections:
   ```bash
   cp -p /etc/nginx/sites-available/claude.onnmed.com.conf /root/claude.onnmed.com.conf.bak-$(date +%Y%m%d)
   sed -i 's#127.0.0.1:13080#127.0.0.1:13090#; s#127.0.0.1:13000#127.0.0.1:13090#' \
     /etc/nginx/sites-available/claude.onnmed.com.conf
   nginx -t && systemctl reload nginx
   curl -sI https://claude.onnmed.com/api/health | grep -i x-deployment-slot   # blue
   ```

5. **Record the active slot and stop the old app containers.** Stop them, don't remove them, so
   rollback stays possible:
   ```bash
   printf 'active_slot=blue\nactive_project=claude-code-proxy-blue\nactive_priority=101\n' > .blue-green-state
   docker compose stop backend frontend quota-refresher notification-dispatcher archive-retention
   ```
   Wait at least 10 min before step 5 if Claude Code streams are in flight on the old containers
   (nginx `proxy_read_timeout` is 600 s).

## Phase 3: test (about 10 min)

```bash
scripts/probe-availability.sh https://claude.onnmed.com/api/health & probe_pid=$!
make deploy-blue-green          # blue -> green
kill "$probe_pid"; wait "$probe_pid" || true
scripts/blue-green.sh status
```
Pass condition: the probe reports `failures=0`, and `status` shows `green` active.

## Rollback (about 1 min, any step)

```bash
cp -p /root/claude.onnmed.com.conf.bak-<date> /etc/nginx/sites-available/claude.onnmed.com.conf
nginx -t && systemctl reload nginx
cd /root/claude-code-proxy && cp -p .env.bak-<timestamp> .env
docker compose up -d --no-build backend frontend quota-refresher notification-dispatcher archive-retention
docker compose -p claude-code-proxy-blue down    # and/or -green; never touches postgres
```

## Known costs

- **Overlap (about 2 min per deploy):** two quota refreshers probe Anthropic's usage endpoint, which
  already returns 429s for the Ultra account. Two archive-retention copies contend for the borg lock.
- **Drain:** the old slot runs up to `BG_DRAIN_SECONDS=600` after a switch so streaming requests finish.
- **Concurrency:** per-account concurrency limits are per process, so they're briefly doubled during overlap.
- **Images:** both slots tag `claude-code-proxy/{backend,frontend}:latest`. Rolling back to an older image
  needs `BG_KEEP_OLD=1` on the deploy (keeps the previous slot's containers running).
