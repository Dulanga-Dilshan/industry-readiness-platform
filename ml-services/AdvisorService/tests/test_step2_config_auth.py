import json

import grpc
import pytest

from advisor.config.runtime_config import ConfigError, RuntimeConfig
from advisor.config.settings import Settings, default_providers

KNOWN = {p.name for p in default_providers()}
from advisor.server.proto import advisor_pb2 as pb
from advisor.server.proto import advisor_pb2_grpc as pbg
from ml_common.auth import SecretInterceptor, secrets_match


def test_config_survives_restart(tmp_path):
    path = str(tmp_path / "rc.json")
    c = RuntimeConfig(path)
    c.update({"tiers": {"small": ["groq-gpt-oss-20b"]}, "cache": {"enabled": True}})
    c2 = RuntimeConfig(path)
    assert c2.get("tiers", "small") == ["groq-gpt-oss-20b"]
    assert c2.get("cache", "enabled") is True
    assert c2.get("cache", "threshold") == 0.95      # untouched defaults still present


def test_broken_config_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "rc.json"
    p.write_text("{not json")
    assert RuntimeConfig(str(p)).get("history_turns") == 6


def test_secrets_are_refused_in_config(tmp_path):
    c = RuntimeConfig(str(tmp_path / "rc.json"))
    with pytest.raises(ConfigError):
        c.update({"providers": {"x": {"api_key": "sk-123"}}})
    with pytest.raises(ConfigError):
        c.update({"admin_key": "abc"})
    assert "sk-" not in json.dumps(c.snapshot())


def test_validation_rejects_unknown_provider_and_bad_budget():
    c = RuntimeConfig(known_providers=KNOWN)
    with pytest.raises(ConfigError):
        c.update({"tiers": {"small": ["groq-gpt-oss-20b", "zzz"]}})
    with pytest.raises(ConfigError):
        c.update({"budgets": {"llm_calls": {"easy": 0}}})
    assert "zzz" not in c.get("tiers", "small")      # failed update changed nothing


def test_set_provider_overrides_limited_fields():
    c = RuntimeConfig(known_providers=KNOWN)
    c.set_provider("groq-gpt-oss-20b", enabled=False, personal_data_ok=True)
    assert c.get("providers", "groq-gpt-oss-20b") == {"enabled": False, "personal_data_ok": True}
    with pytest.raises(ConfigError):
        c.set_provider("groq-gpt-oss-20b", base_url="http://evil")
    with pytest.raises(ConfigError):
        c.set_provider("nope", enabled=True)


def test_secrets_match_is_constant_time_function_and_handles_empty():
    import ml_common.auth as auth
    import inspect
    assert "compare_digest" in inspect.getsource(auth.secrets_match)
    assert secrets_match("abc", "abc") and not secrets_match("abc", "abd")
    assert not secrets_match("", "") and not secrets_match(None, "x") and not secrets_match("x", "")


def test_settings_validation():
    s = Settings()
    assert len(s.validate()) == 2
    s.service_secret = s.admin_key = "same"
    assert any("different" in p for p in s.validate())
    s.admin_key = "other"
    assert s.validate() == []


class Servicer(pbg.AdvisorServiceServicer):
    async def GetStatus(self, request, context):
        return pb.Status(version="t")

    async def SetModel(self, request, context):
        return pb.ModelConfig()

    async def Ask(self, request, context):
        yield pb.AskEvent(type="token", data_json="{}")


@pytest.fixture
async def server():
    interceptor = SecretInterceptor("svc-secret", "admin-key", {"SetModel"})
    srv = grpc.aio.server(interceptors=[interceptor])
    pbg.add_AdvisorServiceServicer_to_server(Servicer(), srv)
    port = srv.add_insecure_port("127.0.0.1:0")
    await srv.start()
    chan = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    yield pbg.AdvisorServiceStub(chan)
    await chan.close()
    await srv.stop(None)


async def test_missing_or_bad_secret_rejected(server):
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await server.GetStatus(pb.Empty())
    assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await server.GetStatus(pb.Empty(), metadata=(("x-service-secret", "wrong"),))
    assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_good_secret_accepted_and_stream_protected(server):
    md = (("x-service-secret", "svc-secret"),)
    assert (await server.GetStatus(pb.Empty(), metadata=md)).version == "t"
    events = [e async for e in server.Ask(pb.AskRequest(question="q"), metadata=md)]
    assert events[0].type == "token"
    with pytest.raises(grpc.aio.AioRpcError):
        [e async for e in server.Ask(pb.AskRequest(question="q"))]


async def test_admin_method_needs_admin_key(server):
    md = (("x-service-secret", "svc-secret"),)
    with pytest.raises(grpc.aio.AioRpcError) as e:
        await server.SetModel(pb.SetModelRequest(), metadata=md)
    assert e.value.code() == grpc.StatusCode.UNAUTHENTICATED
    ok = await server.SetModel(pb.SetModelRequest(), metadata=md + (("x-admin-key", "admin-key"),))
    assert isinstance(ok, pb.ModelConfig)
    # the service secret is not an admin key
    with pytest.raises(grpc.aio.AioRpcError):
        await server.SetModel(pb.SetModelRequest(), metadata=md + (("x-admin-key", "svc-secret"),))
