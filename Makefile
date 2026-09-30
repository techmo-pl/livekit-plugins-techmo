.PHONY: install install-dev lint format typecheck test clean

# Install the package in editable/dev mode
install-dev:
	pip install -e ".[dev]"

install:
	pip install -e .

lint:
	ruff check livekit/ tests/ examples/

format:
	ruff format livekit/ tests/ examples/

typecheck:
	mypy livekit/plugins/techmo/

test:
	pytest tests/ -v

clean:
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
