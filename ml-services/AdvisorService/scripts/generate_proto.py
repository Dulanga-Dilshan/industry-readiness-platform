"""Regenerate advisor_pb2.py and advisor_pb2_grpc.py (run from the AdvisorService folder).

    uv run python scripts/generate_proto.py
"""
import pathlib
import sys

from grpc_tools import protoc

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PROTO = SRC / "advisor" / "server" / "proto" / "advisor.proto"

code = protoc.main(["grpc_tools.protoc", f"-I{SRC}", f"--python_out={SRC}", f"--grpc_python_out={SRC}", str(PROTO)])
sys.exit(code)
