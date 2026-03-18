"""Hatchling build hook: generates gRPC/protobuf stubs from .proto files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


PROTO_DIR = Path(__file__).parent / "proto"
OUTPUT_DIR = Path(__file__).parent / "livekit" / "plugins" / "techmo" / "_proto"

PROTO_FILES = [
    "techmo/asr/api/v1p1/asr.proto",
]


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        _generate_protos()


def _generate_protos() -> None:
    try:
        from grpc_tools import protoc
    except ImportError:
        print(
            "WARNING: grpcio-tools not found. Skipping proto generation. "
            "Run 'pip install grpcio-tools && python -m grpc_tools.protoc ...' manually.",
            file=sys.stderr,
        )
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write __init__.py so the package is importable
    init_file = OUTPUT_DIR / "__init__.py"
    if not init_file.exists():
        init_file.write_text("# Auto-generated gRPC stubs package\n")

    # Collect include paths: our proto dir + grpc_tools bundled protos
    import grpc_tools

    grpc_tools_proto_dir = Path(grpc_tools.__file__).parent / "_proto"
    include_paths = [str(PROTO_DIR), str(grpc_tools_proto_dir)]
    include_args = [f"-I{p}" for p in include_paths]

    for proto_file in PROTO_FILES:
        args = [
            "grpc_tools.protoc",
            *include_args,
            f"--python_out={OUTPUT_DIR}",
            f"--grpc_python_out={OUTPUT_DIR}",
            f"--pyi_out={OUTPUT_DIR}",
            proto_file,
        ]
        ret = protoc.main(args)
        if ret != 0:
            raise RuntimeError(f"protoc failed for {proto_file} (exit code {ret})")

    # Fix relative imports in generated files (protoc generates absolute imports)
    _fix_grpc_imports(OUTPUT_DIR)

    print(f"gRPC stubs generated in {OUTPUT_DIR}")


def _fix_grpc_imports(output_dir: Path) -> None:
    """Replace absolute package imports with relative ones in generated *_pb2_grpc.py files."""
    for grpc_file in output_dir.glob("**/*_pb2_grpc.py"):
        content = grpc_file.read_text()
        # protoc generates: from techmo.asr.api.v1p1 import asr_pb2 as ...
        # We need relative imports since the stubs are nested under _proto/
        # This simple replacement handles the v1p1 case
        content = content.replace(
            "from techmo.asr.api.v1p1 import asr_pb2",
            "from . import asr_pb2",
        )
        grpc_file.write_text(content)


if __name__ == "__main__":
    _generate_protos()
