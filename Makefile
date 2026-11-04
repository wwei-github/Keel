.PHONY: help setup test test-pg lint api web up down logs pg-up pg-down backfill etl acceptance

UV := UV_CACHE_DIR=$(CURDIR)/backend/.uv-cache uv

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

# 本地 PG 只用容器：本机不装原生 PG，省掉版本与 locale 两个变量
pg-up:
	docker compose -f deploy/docker-compose.yml up -d postgres

pg-down:
	docker compose -f deploy/docker-compose.yml down postgres

backfill:
	cd backend && $(UV) run python -c "from keel.tools.backfill import run_for_enabled_instruments as r; print(r())"

etl:
	cd backend && $(UV) run python -c "from keel.data.etl import run_once; print(run_once())"

acceptance:
	cd backend && $(UV) run python -m keel.tools.acceptance

up:
	docker compose -f deploy/docker-compose.yml up --build

down:
	docker compose -f deploy/docker-compose.yml down

logs:
	docker compose -f deploy/docker-compose.yml logs -f --tail=100
