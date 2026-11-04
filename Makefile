.PHONY: help setup test test-pg lint api web up down logs pg-up pg-down migrate instruments backfill etl acceptance

UV := UV_CACHE_DIR=$(CURDIR)/backend/.uv-cache uv

# macOS 上 buildx 会报 `failed to update builder last activity time ... operation
# not permitted`（buildx 的 activity 目录权限问题，不是 daemon 的问题）。
# 强制走旧构建器绕开它；Linux 上可以 BUILDKIT=1 恢复默认。
BUILDKIT ?= 0

help:
	@grep -E '^[a-zA-Z_-]+:' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":"}{printf "  %-10s\n", $$1}'

setup:
	cd backend && $(UV) sync
	cd web && npm install

test:
	cd backend && $(UV) run pytest

# 带 PG 的测试。不带这个变量时 test_db/test_queue/test_ops/test_bus 会整文件 skip
test-pg:
	cd backend && KEEL_TEST_PG_DSN="postgresql://keel:keel@127.0.0.1:5432/keel" $(UV) run pytest

lint:
	cd backend && $(UV) run ruff check . && $(UV) run ruff format --check .
	cd web && npm run typecheck

api:
	cd backend && $(UV) run uvicorn keel.api.app:app --reload --port 8000

web:
	cd web && npm run dev

# compose 需要 .env（env_file 指向它）。不存在就从 example 复制一份。
env:
	@test -f .env || cp .env.example .env

# 本地 PG 只用容器：本机不装原生 PG，省掉版本与 locale 两个变量
pg-up: env
	docker compose -f deploy/docker-compose.yml up -d postgres

pg-down:
	docker compose -f deploy/docker-compose.yml down postgres

# 建表。幂等，第一次跑和第一百次跑一样。
migrate:
	cd backend && $(UV) run python -c "from keel.config import get_settings; from keel.db.migrations import migrate; migrate(get_settings().pg_dsn); print('migrated')"

# 同步交易对主数据。必须在 backfill 之前跑，否则 backfill 找不到标的、静默空转。
instruments:
	cd backend && $(UV) run python -c "from keel.market.instruments import sync_instruments; print(sync_instruments())"

backfill: instruments
	cd backend && $(UV) run python -c "from keel.tools.backfill import run_for_enabled_instruments as r; print(r())"

etl:
	cd backend && $(UV) run python -c "from keel.data.etl import run_once; print(run_once())"

acceptance:
	cd backend && $(UV) run python -m keel.tools.acceptance

up:
	DOCKER_BUILDKIT=$(BUILDKIT) docker compose -f deploy/docker-compose.yml up -d --build

down:
	docker compose -f deploy/docker-compose.yml down

logs:
	docker compose -f deploy/docker-compose.yml logs -f --tail=100
