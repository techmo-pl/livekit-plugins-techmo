.PHONY: proto install install-dev lint format typecheck test clean

PROTO_DIR := proto
OUTPUT_DIR := livekit/plugins/techmo/_proto

# Generate gRPC/protobuf Python stubs from .proto files
proto:
	pip install grpcio-tools --quiet
	python hatch_build.py

# Install the package in editable/dev mode (also generates protos)
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
	rm -rf $(OUTPUT_DIR)/techmo
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
