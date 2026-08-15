.PHONY: build up down logs shell test test-all lint fmt clean reset clean-deploys clean-deploys-all

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f app

shell:
	docker compose exec app bash

test:
	venv/bin/pytest -v -m "not slow"

test-all:
	venv/bin/pytest -v

lint:
	venv/bin/ruff check deploymint tests

fmt:
	venv/bin/ruff format deploymint tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info

reset:
	docker compose down -v
	docker compose up -d

# Deletes managed-by=deploymint deployments/services/containers — see
# docs/08-phase-4-execution.md §4.8.
clean-deploys:
	docker compose exec app python -m deploymint.scripts.clean

clean-deploys-all:
	docker compose exec app python -m deploymint.scripts.clean --all
