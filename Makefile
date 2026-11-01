# Compose files come from COMPOSE_FILE (shell, then .env, then the default) so make and plain
# `docker compose` always manage the same services. Leaving a file out (e.g. docker-compose.postgres.yml)
# makes its containers look like orphans; deploy therefore never passes --remove-orphans.
COMPOSE_FILE ?= $(or $(shell sed -n 's/^COMPOSE_FILE=//p' .env 2>/dev/null),docker-compose.yaml:docker-compose.live.yml)
export COMPOSE_FILE
COMPOSE := docker compose
DEPLOY_WAIT_SECONDS ?= 180

.PHONY: help backend-check frontend-check verify deploy deploy-blue-green deploy-frontend status logs

help:
	@echo "make verify          Run backend tests/lint and frontend lint/build"
	@echo "make deploy          Build, deploy, and health-check every service in one command"
	@echo "make deploy-blue-green Deploy the next blue/green slot with a background smoke probe"
	@echo "make deploy-frontend Build, deploy, and health-check only the frontend"
	@echo "make status          Show the live stack status"
	@echo "make logs            Follow live stack logs"

backend-check:
	cd backend && uv run ruff check .
	cd backend && uv run pytest -q

frontend-check:
	cd frontend && node --test tests/*.test.mjs
	cd frontend && pnpm lint
	cd frontend && pnpm exec prettier --check "src/app/(dashboard)/accounts/page.tsx" "src/app/(dashboard)/fallbacks/page.tsx" "src/app/(dashboard)/notifications/page.tsx" "src/app/(dashboard)/users/page.tsx" src/components/Sidebar.tsx src/lib/api.ts src/lib/types.ts
	cd frontend && pnpm build

verify: backend-check frontend-check

deploy:
	$(COMPOSE) config --quiet
	$(COMPOSE) up -d --build --wait --wait-timeout $(DEPLOY_WAIT_SECONDS)

deploy-blue-green:
	./scripts/blue-green.sh deploy

deploy-frontend:
	$(COMPOSE) config --quiet
	$(COMPOSE) up -d --build --no-deps frontend --wait --wait-timeout $(DEPLOY_WAIT_SECONDS)

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100
