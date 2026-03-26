"""Hatchling build hook: generates gRPC/protobuf stubs from .proto files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


PROTO_DIR = Path(__file__).parent / "proto"
OUTPUT_DIR = Path(__file__).parent / "livekit" / "plugins" / "techmo" / "_proto"

PROTO_FILES = [
    "techmo/api/status.proto",
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

    # Ensure every subdirectory under OUTPUT_DIR is a proper Python package
    _ensure_init_files(OUTPUT_DIR)

    # Fix absolute imports in all generated files (protoc generates absolute imports)
    _fix_proto_imports(OUTPUT_DIR)

    print(f"gRPC stubs generated in {OUTPUT_DIR}")


def _ensure_init_files(output_dir: Path) -> None:
    """Create __init__.py in every subdirectory so they are importable packages."""
    for dirpath in output_dir.rglob("*"):
        if dirpath.is_dir():
            init = dirpath / "__init__.py"
            if not init.exists():
                init.write_text("")


def _fix_proto_imports(output_dir: Path) -> None:
    """Replace absolute package imports with relative ones in all generated pb2 files.

    protoc generates absolute imports like ``from techmo.x import y_pb2`` which
    break when the stubs are nested under ``_proto/``.  We compute the correct
    relative prefix for each file based on its depth under output_dir.
    """
    for pb_file in output_dir.glob("**/*.py"):
        content = pb_file.read_text()
        if "from techmo." not in content:
            continue

        # Compute how many package levels separate this file from output_dir.
        # Each level = one dot in the relative import prefix.
        depth = len(pb_file.relative_to(output_dir).parts) - 1  # -1 for the file itself
        prefix = "." * depth  # e.g. depth=4 → "...." for _proto/techmo/asr/api/v1p1/

        # Replace every absolute "from techmo.<sub> import" with a relative one.
        # e.g. depth=4, "from techmo.api import" → "from ....api import"
        import re
        content = re.sub(
            r"from techmo((?:\.\w+)*) import",
            lambda m: f"from {prefix}{m.group(1).lstrip('.')} import",
            content,
        )
        pb_file.write_text(content)


if __name__ == "__main__":
    _generate_protos()
