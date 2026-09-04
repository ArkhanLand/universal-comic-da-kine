.PHONY: check format format-check lint typecheck test

check: format-check lint typecheck test

format:
	ruff format .

format-check:
	ruff format --check .

lint:
	ruff check .

lint-fix:
	ruff check --fix .

typecheck:
	mypy src

test:
	pytest -v -m "not network"

test-network:
	pytest -v -m "network"
