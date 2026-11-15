"""Evaluation runner: executes the question set, checks expectations with code, measures tokens and time."""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid

import httpx
import yaml

from advisor.agents.verifier import numbers_in, SMALL_INT_MAX
from advisor.orchestrator import AskInput
from ml_common import events as ev

QUESTIONS = os.path.join(os.path.dirname(__file__), "questions.yaml")


def final_text(events) -> str:
    out = ""
    for e in events:
        if e.type == ev.RESET:
            out = ""
        elif e.type == ev.TOKEN:
            out += e.data["text"]
    return out.strip()


def check(item: dict, events: list, evidence_hint: str = "") -> list[str]:
    """Returns a list of failed expectations (empty = pass)."""
    exp = item.get("expect", {})
    fails: list[str] = []
    done = next((e.data for e in reversed(events) if e.type == ev.DONE), {})
    errors = [e for e in events if e.type == ev.ERROR]
    text = final_text(events)
    if errors:
        fails.append(f"error event: {errors[0].data.get('code')}")
    if "intent" in exp and done.get("intent") != exp["intent"]:
        fails.append(f"intent {done.get('intent')!r} != {exp['intent']!r}")
    tools = done.get("tools", [])
    for t in exp.get("tools_include", []):
        if t not in tools:
            fails.append(f"tool {t} not used (used: {tools})")
    for t in exp.get("tools_exclude", []):
        if t in tools:
            fails.append(f"tool {t} should not be used")
    for s in exp.get("answer_contains", []):
        if s.lower() not in text.lower():
            fails.append(f"answer missing '{s}'")
    for s in exp.get("answer_not_contains", []):
        if s.lower() in text.lower():
            fails.append(f"answer contains '{s}'")
    codes = {e.data.get("code") for e in events if e.type == ev.NOTE}
    for c in exp.get("notes_include", []):
        if c not in codes:
            fails.append(f"note {c} missing (notes: {sorted(codes)})")
    if exp.get("refused") and (done.get("llm_calls", 0) != 0):
        fails.append("expected a refusal without a model call")
    if "sources_min" in exp and len([e for e in events if e.type == ev.SOURCE]) < exp["sources_min"]:
        fails.append("too few sources")
    if "max_llm_calls" in exp and done.get("llm_calls", 0) > exp["max_llm_calls"]:
        fails.append(f"llm_calls {done.get('llm_calls')} > {exp['max_llm_calls']}")
    if "corrected" in exp and bool([e for e in events if e.type == ev.CORRECTED]) != exp["corrected"]:
        fails.append("spelling correction expectation not met")
    if exp.get("asks"):
        wanted = {"semester": "Which semester", "cluster": "Which career cluster"}[exp["asks"]]
        if wanted not in text:
            fails.append(f"expected a clarifying question ({exp['asks']})")
    if exp.get("no_unsupported_numbers"):
        src_numbers = set()
        for e in events:
            if e.type == ev.SOURCE and e.data.get("as_of"):
                src_numbers.add(e.data["as_of"])
        # strict check lives in the verifier; here we only flag numbers no source could back (no sources at all)
        nums = [n for n in numbers_in(text) if not (n.is_integer() and (n <= SMALL_INT_MAX or 1990 <= n <= 2100))]
        if nums and not [e for e in events if e.type == ev.SOURCE and e.data.get("kind") in ("backend", "curriculum")]:
            fails.append(f"numbers {nums} in answer but no data source")
    return fails


class EvalRunner:
    def __init__(self, app):
        self.app = app
        self.runs: dict[str, dict] = {}

    def load_questions(self) -> list[dict]:
        with open(QUESTIONS, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or []

    def start(self, opts: dict) -> dict:
        rid = uuid.uuid4().hex[:8]
        self.runs[rid] = {"id": rid, "state": "running", "results": [], "summary": {}}
        asyncio.create_task(self._run(rid, opts))
        return {"run_id": rid}

    async def _set_backend(self, mode: str) -> None:
        mock = getattr(self.app, "_mock_transport", None)
        if mock is None:
            return
        async with httpx.AsyncClient(transport=mock, base_url="http://mock") as c:
            await c.post("/mock/mode", json={"mode": mode})

    async def _run(self, rid: str, opts: dict) -> None:
        run = self.runs[rid]
        qs = self.load_questions()
        if opts.get("ids"):
            qs = [q for q in qs if q["id"] in opts["ids"]]
        saved_tiers = None
        if opts.get("provider"):          # pin every tier to one provider (bake-off)
            saved_tiers = {k: list(v) for k, v in self.app.router.tiers.items()}
            self.app.router.tiers = {k: [opts["provider"]] for k in saved_tiers}
            self.app.router.fallback = {}
        self.app.limiter.per_user = 10**6
        t_all = time.time()
        try:
            for q in qs:
                await self._set_backend(q.get("backend", "ok"))
                if q.get("profile"):
                    await self.app.upsert_profile(q["profile"])
                t0 = time.time()
                events = []
                try:
                    async for e in self.app.ask_events(AskInput(q["question"], (q.get("profile") or {}).get("user_id", ""))):
                        events.append(e)
                except Exception as e:
                    events.append(ev.error("exception", type(e).__name__))
                fails = check(q, events)
                done = next((e.data for e in reversed(events) if e.type == ev.DONE), {})
                run["results"].append({"id": q["id"], "category": q.get("category"), "passed": not fails, "fails": fails,
                                       "seconds": round(time.time() - t0, 2), "llm_calls": done.get("llm_calls"),
                                       "providers": done.get("providers"), "answer": final_text(events)[:500]})
                if opts.get("delay"):
                    await asyncio.sleep(float(opts["delay"]))
        finally:
            await self._set_backend("ok")
            if saved_tiers:
                self.app.apply_runtime()
        res = run["results"]
        by_cat: dict[str, list] = {}
        for r in res:
            by_cat.setdefault(r["category"], []).append(r["passed"])
        run["summary"] = {"total": len(res), "passed": sum(r["passed"] for r in res),
                          "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in by_cat.items()},
                          "seconds": round(time.time() - t_all, 1),
                          "tokens": self.app.ledger.aggregate(time.time() - t_all + 1)}
        run["state"] = "done"

    async def capacity_test(self, opts: dict) -> dict:
        n = int(opts.get("concurrent", 5))
        q = opts.get("question", "What is Docker?")
        self.app.limiter.per_user = 10**6
        reroutes0 = self.app.router.reroutes

        async def one(i: int):
            t0 = time.time()
            events = [e async for e in self.app.ask_events(AskInput(q, f"cap-{i}"))]
            return time.time() - t0, any(e.type == ev.ERROR for e in events)
        res = await asyncio.gather(*[one(i) for i in range(n)])
        lat = sorted(r[0] for r in res)
        return {"concurrent": n, "p50_s": round(lat[len(lat) // 2], 2), "max_s": round(lat[-1], 2),
                "errors": sum(1 for r in res if r[1]), "reroutes": self.app.router.reroutes - reroutes0,
                "quota": self.app.quota_report()}
