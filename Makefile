.PHONY: help setup test lint api web up down logs

UV := UV_CACHE_DIR=$(CURDIR)/backend/.uv-cache uv

help:
	@grep -E '^[a-zA-Z_-]+:' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":"}{printf "  %-10s\n", $$1}'

setup:
	cd backend && $(UV) sync

test:
	cd backend && $(UV) run pytest

lint:
	cd backend && $(UV) run ruff check . && $(UV) run ruff format --check .
	cd web && npm run typecheck

api:
	cd backend && $(UV) run uvicorn keel.api.app:app --reload --port 8000

web:
	cd web && npm run dev

up:
	docker compose -f deploy/docker-compose.yml up --build

down:
	docker compose -f deploy/docker-compose.yml down

logs:
	docker compose -f deploy/docker-compose.yml logs -f --tail=100
