"""Regenerate src/grpc_server/proto/jobs_pb2*.py after editing jobs.proto.

    uv run python scripts/generate_proto.py
"""
import sys
from pathlib import Path

import grpc_tools
from grpc_tools import protoc

SRC = Path(__file__).resolve().parents[1] / "src"
PROTO = SRC / "grpc_server" / "proto" / "jobs.proto"
# Google's built-in types (timestamp.proto ...) ship inside grpc_tools
WELL_KNOWN = Path(grpc_tools.__file__).parent / "_proto"

sys.exit(
    protoc.main(
        [
            "protoc",
            f"-I{SRC}",
            f"-I{WELL_KNOWN}",
            f"--python_out={SRC}",
            f"--grpc_python_out={SRC}",
            str(PROTO),
        ]
    )
)
