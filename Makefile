.PHONY: install install-dev lint format typecheck test test-integration clean

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
	pytest tests/ -v --ignore=tests/test_integration.py --ignore=tests/test_tts_integration.py

# Integration tests against running services (set TECHMO_ASR_ADDRESS and/or TECHMO_TTS_ADDRESS)
test-integration:
	pytest tests/test_integration.py tests/test_tts_integration.py -v

clean:
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
