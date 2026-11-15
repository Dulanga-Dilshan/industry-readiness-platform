"""Starts the real entrypoint (python -m advisor) with fake models and talks to it over gRPC and REST."""
import os
import subprocess
import sys
import time

import grpc
import httpx

from advisor.server.proto import advisor_pb2 as pb
from advisor.server.proto import advisor_pb2_grpc as pbg

ROOT = os.path.join(os.path.dirname(__file__), "..")


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start(env_extra):
    gp, rp = free_port(), free_port()
    env = {**os.environ, "PYTHONPATH": f"{ROOT}/src:{ROOT}", "GRPC_HOST": "127.0.0.1", "GRPC_PORT": str(gp),
           "REST_HOST": "127.0.0.1", "REST_PORT": str(rp), "DATA_DIR": ":memory:", "ADVISOR_USE_FAKES": "1",
           "KNOWLEDGE_DIR": f"{ROOT}/tests/fixtures/kb_dev", "CURRICULUM_PATH": f"{ROOT}/tests/fixtures/curriculum_ok.yaml",
           "GLOSSARY_PATH": f"{ROOT}/data/skills_glossary.txt", "EMBEDDING_PROVIDER": "fake", **env_extra}
    return subprocess.Popen([sys.executable, "-m", "advisor"], env=env, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True), gp, rp


def test_refuses_to_start_without_secrets():
    proc, _, _ = start({"SERVICE_SECRET": "", "ADMIN_KEY": ""})
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 2 and "SERVICE_SECRET is not set" in out


def test_real_process_serves_grpc_and_rest():
    proc, gp, rp = start({"SERVICE_SECRET": "svc", "ADMIN_KEY": "adm"})
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{rp}/advisor/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise AssertionError("server did not start: " + (proc.stdout.read() if proc.poll() else ""))
        for _ in range(50):
            if httpx.get(f"http://127.0.0.1:{rp}/advisor/health").json()["index"] == "ready":
                break
            time.sleep(0.2)
        chan = grpc.insecure_channel(f"127.0.0.1:{gp}")
        stub = pbg.AdvisorServiceStub(chan)
        st = stub.GetStatus(pb.Empty(), metadata=(("x-service-secret", "svc"),))
        assert st.index_state == "ready" and st.curriculum_status == "ok"
        events = list(stub.Ask(pb.AskRequest(question="What is Docker?"), metadata=(("x-service-secret", "svc"),)))
        assert events[-1].type == "done"
        try:
            stub.GetStatus(pb.Empty())
            raise AssertionError("expected UNAUTHENTICATED")
        except grpc.RpcError as e:
            assert e.code() == grpc.StatusCode.UNAUTHENTICATED
    finally:
        proc.terminate()
        proc.wait(timeout=10)
