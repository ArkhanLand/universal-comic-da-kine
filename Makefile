.PHONY: check format format-check lint typecheck test

check: format-check lint typecheck test

format-check:
	ruff format --check .

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -v -m "not network"

format:
	ruff format .

lint-fix:
	ruff check --fix .

test-network:
	pytest -v -m "network"
