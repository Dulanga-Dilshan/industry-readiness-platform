import os

import httpx

from advisor.app import AdvisorApp
from advisor.config.settings import Settings, default_providers
from advisor.orchestrator import AskInput
from advisor.tools.web_search import FakeWebProvider
from ml_common import events as ev
from ml_common.embeddings.fake import FakeEmbedder
from ml_common.providers.fake import Fail, FakeProvider, Ok
from ml_common.vectorstore.memory_store import MemoryVectorStore
from mock_backend.app import create_app

HERE = os.path.dirname(__file__)
KB = os.path.join(HERE, "fixtures", "kb_dev")
CURR = os.path.join(HERE, "fixtures", "curriculum_ok.yaml")
GLOSS = os.path.join(HERE, "..", "data", "skills_glossary.txt")


class Brain:
    """Scripted 'model' behind every fake provider. Tests set the attributes they care about."""

    def __init__(self):
        self.writer = "Here is a helpful answer [S1]."
        self.triage = '{"intent":"knowledge","difficulty":"easy","cluster":null,"rewrite":null}'
        self.verifier = '{"ok": true, "issues": []}'
        self.advice = "Notes: learn Docker first [S1]."
        self.extract = '{"facts":[],"summary":""}'
        self.data_tool_calls = [("get_cluster_demand", {"window": "6m"})]
        self.seen: list[dict] = []          # every (agent, messages) the models saw

    def __call__(self, messages, tools):
        system = messages[0]["content"]
        agent = ("triage" if "Classify the student's message" in system else
                 "verifier" if "Check the ANSWER" in system else
                 "advice" if "planning career advice" in system else
                 "extract" if "extract durable facts" in system else
                 "data" if tools else "writer")
        self.seen.append({"agent": agent, "messages": messages})
        if agent == "triage":
            return Ok(self.triage)
        if agent == "verifier":
            return Ok(self.verifier)
        if agent == "advice":
            return Ok(self.advice)
        if agent == "extract":
            return Ok(self.extract)
        if agent == "data":
            if any(m.get("role") == "tool" for m in messages):
                return Ok("done")
            return Ok("", tool_calls=self.data_tool_calls)
        w = self.writer(messages) if callable(self.writer) else self.writer
        return w if not isinstance(w, str) else Ok(w)

    def agents(self):
        return [s["agent"] for s in self.seen]


async def make_app(brain=None, *, personal=True, web=None, index=True, curriculum=CURR, extra_settings=None, providers=None):
    brain = brain or Brain()
    specs = default_providers()
    provs = providers or {}
    if not providers:
        for s in specs:
            from dataclasses import replace
            spec = replace(s, kind="fake", enabled=not s.name.startswith("ollama"), personal_data_ok=personal)
            provs[s.name] = FakeProvider(spec, responder=brain, name=s.name)
    settings = Settings(service_secret="test-secret", admin_key="admin", data_dir=":memory:", knowledge_dir=KB,
                        curriculum_path=curriculum, glossary_path=GLOSS, backend_base_url="http://mock",
                        **(extra_settings or {}))
    backend = create_app("test-secret", slow_seconds=1.0, timeout_seconds=5.0)
    app = AdvisorApp(settings, providers=provs, embedder=FakeEmbedder(), store=MemoryVectorStore(),
                     backend_transport=httpx.ASGITransport(app=backend),
                     web_providers=web if web is not None else {"wikipedia": FakeWebProvider("wikipedia", [])})
    app.backend_client.backoff = (0.0, 0.0)
    app.rc.update({"backend": {"connect_timeout": 0.5, "read_timeout": 0.3, "total_budget": 2.0}})
    app._mock = backend
    app._mock_transport = httpx.ASGITransport(app=backend)
    if index:
        await app.kb.ensure()
    return app, brain


async def set_mode(app, mode):
    async with httpx.AsyncClient(transport=app._mock_transport, base_url="http://mock") as c:
        await c.post("/mock/mode", json={"mode": mode})


async def ask(app, question, **kw):
    inp = AskInput(question=question, **kw)
    return [e async for e in app.ask_events(inp)]


def text_of(events):
    """Final visible text: tokens after the last reset."""
    out = ""
    for e in events:
        if e.type == ev.RESET:
            out = ""
        elif e.type == ev.TOKEN:
            out += e.data["text"]
    return out.strip()


def of_type(events, t):
    return [e for e in events if e.type == t]


def done(events):
    return of_type(events, ev.DONE)[-1].data
