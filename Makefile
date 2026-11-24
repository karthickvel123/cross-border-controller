.PHONY: test eval-dry lint-boundary run-api

test:
	pytest backend/tests/ -v

eval-dry:
	python -m eval.harness --dry-run --seed 20261122

lint-boundary:
	python scripts/check_boundary.py

run-api:
	uvicorn backend.src.main:app --reload --port 8000
