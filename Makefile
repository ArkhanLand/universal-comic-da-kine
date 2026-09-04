.PHONY: check format format-check lint typecheck test

check: format-check lint typecheck test

format:
	ruff format .

format-check:
	ruff format --check .

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest
