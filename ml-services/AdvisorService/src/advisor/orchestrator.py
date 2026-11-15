"""The pipeline: guard -> spell -> triage -> evidence agents -> (advice) -> writer -> verifier."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

from ml_common import events as ev
from ml_common.events import Event
from ml_common.providers.budget import Block, Prompt
from ml_common.providers.router import AllProvidersFailed, ChatRequest, NoCapableProvider, ToolFailure

from advisor.agents.evidence import Evidence, build_evidence
from advisor.agents.triage import Triage, classify, detect_cluster
from advisor.agents.verifier import check_answer, rewrite_instruction
from advisor.config.settings import CLUSTERS
from advisor.memory.extract import parse_json
from advisor.prompts import all_versions, load_prompt
from advisor.safety.guardrails import screen_input
from advisor.safety.redaction import safe_profile_summary
from advisor.text.spellcheck import normalize
from advisor.tools.base import ToolContext

log = logging.getLogger("advisor.orchestrator")

GREETING = "Hello! I'm the Career Advisor. Ask me about IT careers, skills to learn, tools, or the job market."
OUT_OF_SCOPE = ("I can only help with IT careers, skills, learning paths and the job market, "
                "so I can't help with that one. Is there something about your IT career I can help with?")
ASK_SEMESTER = "Which semester are you in right now (for example 3.1)? I need it to compare the market with your modules."
ASK_CLUSTER = ("Which career cluster do you mean? " + ", ".join(CLUSTERS) + ".")


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    llm_max: int
    tool_max: int
    deadline: float
    llm_used: int = 0

    def use_llm(self, reserve: int = 0) -> None:
        if self.llm_used + 1 > self.llm_max - reserve or time.monotonic() > self.deadline:
            raise BudgetExceeded()
        self.llm_used += 1


@dataclass
class AskInput:
    question: str
    user_id: str = ""
    session_id: str = ""
    turns: list[dict] = field(default_factory=list)
    tier_override: str = ""
    remember: bool = True


class Orchestrator:
    def __init__(self, app):
        self.app = app

    # ---- helpers ------------------------------------------------------------
    def cfg(self, *keys, default=None):
        return self.app.rc.get(*keys, default=default)

    def tool_event(self, name: str, result) -> Event:
        return Event(ev.TOOL, {"name": name, "status": result.status})

    async def run_tool(self, name: str, args: dict, ctx: ToolContext, budget: Budget, out: list[Event]):
        res = await self.app.registry.execute(name, args, ctx)
        out.append(self.tool_event(name, res))
        return res

    # ---- main entry -----------------------------------------------------------
    async def ask(self, req: AskInput) -> AsyncIterator[Event]:
        app = self.app
        trace = uuid.uuid4().hex[:12]
        started = time.monotonic()
        yield ev.status("Reading your question")

        ok, why = app.limiter.check(req.user_id)
        if not ok:
            yield ev.error("rate_limited", why, retryable=True)
            return
        screen = screen_input(req.question, self.cfg("limits", "max_input_chars", default=2000))
        if not screen.ok:
            yield ev.token(screen.message)
            yield Event(ev.DONE, {"trace_id": trace, "refused": screen.reason, "llm_calls": 0})
            return

        question = normalize(req.question)
        sp = app.spell.correct(question)
        if sp.changed:
            yield Event(ev.CORRECTED, {"original": question, "corrected": sp.corrected})
            question = sp.corrected

        profile = app.profiles.get(req.user_id) if req.user_id else None
        triage = classify(question, self.cfg("difficulty", "long_question_words", default=25),
                          self.cfg("difficulty", "hard_words"), app.known_terms)
        budgets = self.cfg("budgets", "llm_calls", default={"easy": 2, "medium": 4, "hard": 7})
        budget = Budget(budgets["hard"], self.cfg("budgets", "tool_calls", default=6),
                        time.monotonic() + self.cfg("budgets", "wall_seconds", default=60))
        tier_map = self.cfg("difficulty", "tier_by_difficulty", default={"easy": "small", "medium": "small", "hard": "large"})
        notes: list[tuple[str, str]] = []
        llm_log: list[str] = []

        try:
            if triage.needs_llm or sp.needs_rewrite:
                triage, question = await self._llm_triage(question, triage, budget, trace, tier_map)
            budget.llm_max = budgets[triage.difficulty]

            if triage.intent == "chitchat":
                yield ev.token(GREETING)
                yield Event(ev.DONE, {"trace_id": trace, "intent": "chitchat", "llm_calls": 0})
                return
            if triage.intent == "out_of_scope":
                yield ev.token(OUT_OF_SCOPE)
                yield Event(ev.DONE, {"trace_id": trace, "intent": "out_of_scope", "llm_calls": 0})
                return

            tier = req.tier_override or tier_map.get(triage.difficulty, "small")
            semester = triage.semester or (profile or {}).get("current_semester") or None
            cluster = triage.cluster or (profile or {}).get("target_cluster") or None

            # ---- semantic cache (knowledge questions without personal data) ----
            cache_scope = f"{app.kb.content_hash}:{sorted(all_versions().items())}:{tier}"
            use_cache = bool(app.rc.get("cache", "enabled")) and triage.intent == "knowledge"
            if use_cache:
                hit = await app.cache.get(question, cache_scope)
                if hit:
                    for s in hit["sources"]:
                        yield Event(ev.SOURCE, s)
                    yield ev.token(hit["answer"])
                    yield Event(ev.DONE, {"trace_id": trace, "intent": triage.intent, "cache": True, "llm_calls": 0})
                    return

            # ---- clarifying questions (code, no model) ----
            if triage.intent == "curriculum" and not semester:
                yield ev.token(ASK_SEMESTER)
                yield Event(ev.DONE, {"trace_id": trace, "intent": triage.intent, "llm_calls": 0, "asked": "semester"})
                return
            if triage.intent == "advice" and not semester:
                yield ev.token(ASK_SEMESTER)
                yield Event(ev.DONE, {"trace_id": trace, "intent": triage.intent, "llm_calls": 0, "asked": "semester"})
                return
            if triage.intent == "advice" and not cluster:
                yield ev.token(ASK_CLUSTER)
                yield Event(ev.DONE, {"trace_id": trace, "intent": triage.intent, "llm_calls": 0, "asked": "cluster"})
                return

            # ---- personal data eligibility ----
            personal_rows = self._personal_rows(profile)
            personal_ok = app.router.can_serve(tier, personal=True)
            ctx = ToolContext(user_id=req.user_id, session_id=req.session_id, trace_id=trace, profile=profile,
                              max_tool_calls=budget.tool_max)
            if personal_rows and not personal_ok:
                notes.append(("personal_dropped", "Personalised details were not used because no private model is "
                                                  "enabled. The answer is general."))
                personal_rows = []
                ctx.profile = None
            elif personal_rows and app.rc.get("memory", "enabled", default=True) is not False:
                personal_rows += await self._memory_rows(question, ctx)

            # ---- evidence agents ----
            yield ev.status("Gathering information")
            tool_events: list[Event] = []
            try:
                await self._gather(triage, question, semester, cluster, ctx, budget, tool_events, trace, tier_map)
            except ToolFailure:
                for e in tool_events:
                    yield e
                yield ev.token(self.cfg("tool_failure_message"))
                yield Event(ev.DONE, {"trace_id": trace, "intent": triage.intent, "tool_failure": True,
                                      "llm_calls": budget.llm_used})
                return
            for e in tool_events:
                yield e

            profile_ctx = []
            if semester:
                profile_ctx.append(f"Current semester: {semester}")
            if cluster:
                profile_ctx.append(f"Career cluster of interest: {cluster}")
            evidence = build_evidence(ctx.results, profile_ctx, personal_rows)
            notes += self._notes_from(ctx.results, triage)
            evidence.notes = notes

            # ---- advice agent (hard) ----
            advice_text = ""
            if triage.intent == "advice":
                try:
                    budget.use_llm(reserve=1)
                    yield ev.status("Planning advice")
                    advice_text = await self._advice(question, evidence, budget, trace, bool(personal_rows))
                except BudgetExceeded:
                    pass
                except (AllProvidersFailed, NoCapableProvider) as e:
                    log.warning("advice agent skipped: %s", e)

            # ---- writer (streamed) ----
            yield ev.status("Writing the answer")
            prompt = self._writer_prompt(question, req, evidence, advice_text)
            answer = ""
            usage_providers: list[str] = []
            wreq = ChatRequest(tier=tier, prompt=prompt, max_tokens=self.cfg("budgets", "max_output_tokens", "writer", default=900),
                               agent="writer", trace_id=trace, personal=bool(personal_rows), visible=True)
            budget.use_llm()
            async for e in app.router.stream(wreq):
                if e.type == ev.TOKEN:
                    answer += e.data["text"]
                    yield e
                elif e.type == ev.RESET:
                    answer = ""
                    yield e
                elif e.type in (ev.REROUTING, ev.STATUS):
                    yield e
                elif e.type == ev.MODEL:
                    usage_providers.append(e.data["provider"])

            # ---- verifier (rules) and one rewrite ----
            secrets_list = [app.settings.service_secret, app.settings.admin_key]
            verdict = check_answer(answer, evidence.text, question, evidence.markers, secrets_list)
            if verdict.needs_rewrite:
                yield Event(ev.RESET, {})
                yield ev.status("Double-checking the answer")
                prompt.extra = [{"role": "assistant", "content": answer},
                                {"role": "user", "content": rewrite_instruction(verdict)}]
                answer = ""
                budget.llm_used += 1        # the rewrite is reserved: it never fails on budget
                async for e in app.router.stream(ChatRequest(tier=tier, prompt=prompt, agent="writer_rewrite",
                                                              max_tokens=900, trace_id=trace,
                                                              personal=bool(personal_rows), visible=True)):
                    if e.type == ev.TOKEN:
                        answer += e.data["text"]
                        yield e
                    elif e.type in (ev.REROUTING, ev.RESET):
                        if e.type == ev.RESET:
                            answer = ""
                        yield e
                verdict = check_answer(answer, evidence.text, question, evidence.markers, secrets_list)
                if verdict.leaked or verdict.unsupported_numbers:
                    yield Event(ev.RESET, {})
                    answer = ("I could not produce a reliable answer with verified numbers. "
                              "Please rephrase your question or try again.")
                    yield ev.token(answer)
            elif verdict.text != answer:         # invalid citation markers removed: send the clean text
                yield Event(ev.RESET, {})
                answer = verdict.text
                yield ev.token(answer)

            # ---- optional LLM verifier (hard) ----
            if triage.difficulty == "hard" and self.cfg("verifier", "llm_for_hard", default=True):
                issue = await self._llm_verify(answer, evidence, budget, trace)
                if issue:
                    notes.append(("unverified", "Some details in this answer could not be fully checked against the "
                                                "available evidence. Treat them with care."))

            for code, text in notes:
                yield ev.note(text, code)
            for s in evidence.sources:
                yield Event(ev.SOURCE, s)

            if use_cache and answer and not notes and not any(s["kind"] == "web" for s in evidence.sources):
                await app.cache.put(question, cache_scope, answer, evidence.sources)

            yield Event(ev.DONE, {"trace_id": trace, "intent": triage.intent, "difficulty": triage.difficulty,
                                  "tier": tier, "llm_calls": budget.llm_used, "tools": [c[0] for c in ctx.calls],
                                  "providers": usage_providers, "cache": False,
                                  "seconds": round(time.monotonic() - started, 2),
                                  "trimmed": prompt.trimmed, "profile": safe_profile_summary(profile)})
        except ToolFailure:
            yield ev.token(self.cfg("tool_failure_message"))
            yield Event(ev.DONE, {"trace_id": trace, "tool_failure": True})
        except NoCapableProvider as e:
            yield ev.error("no_provider", "No model is available for this request right now.", retryable=False)
            log.warning("no capable provider: %s", e)
        except AllProvidersFailed as e:
            yield ev.error("all_providers_failed", "The advisor is busy right now. Please try again in a moment.",
                           retryable=True)
            log.warning("all providers failed: %s", e)
        except BudgetExceeded:
            yield ev.error("budget", "This question needed more steps than allowed. Please ask something narrower.")

    # ---- agents -------------------------------------------------------------------
    @staticmethod
    def _personal_rows(profile: dict | None) -> list[str]:
        if not profile or profile.get("consent", {}).get("store_memory") is False:
            return []
        rows = [f"Goal: {g}" for g in profile.get("goals", [])] + \
               [f"Constraint: {c}" for c in profile.get("constraints", [])] + \
               [f"Skill the student says they have: {s}" for s in profile.get("self_assessed_skills", [])] + \
               [f"Remembered ({f['type']}): {f['text']}" for f in profile.get("facts", [])]
        return rows

    async def _memory_rows(self, question: str, ctx: ToolContext) -> list[str]:
        res = await self.app.registry.execute("search_user_memory", {"query": question[:200]}, ctx)
        if not res.usable:
            return []
        return [f"Earlier conversation: {h['text']}" for h in res.data.get("hits", [])]

    async def _gather(self, t: Triage, question, semester, cluster, ctx, budget, out, trace, tier_map) -> None:
        app = self.app
        run = lambda name, args: self.run_tool(name, args, ctx, budget, out)
        tasks = []
        if t.intent == "knowledge":
            await self._knowledge(question, cluster, run)
            return
        if t.intent == "curriculum":
            tasks.append(run("get_curriculum_progress", {"semester": semester}))
            if cluster:
                tasks.append(run("get_skill_gaps", {"semester": semester, "cluster": cluster, "window": t.window}))
            await asyncio.gather(*tasks)
            return
        if t.intent == "advice":
            await asyncio.gather(
                run("get_skill_gaps", {"semester": semester, "cluster": cluster, "window": t.window}),
                run("get_curriculum_progress", {"semester": semester}),
                self._knowledge(f"learning path {cluster} {question}", cluster, run, web_fallback=False))
            return
        if t.intent == "data":
            plan = self._data_plan(t, cluster)
            if plan is None:
                await self._data_llm(question, ctx, budget, out, trace, tier_map.get(t.difficulty, "small"))
            else:
                await asyncio.gather(*[run(n, a) for n, a in plan])

    @staticmethod
    def _data_plan(t: Triage, cluster: str | None):
        if "jobs" in t.wants:
            return None                         # free-form arguments: let the model pick the tool
        plan = []
        if "demand" in t.wants:
            plan.append(("get_cluster_demand", {"window": t.window}))
        if "top_skills" in t.wants:
            if not cluster:
                return None
            plan.append(("get_top_skills", {"cluster": cluster, "window": t.window}))
        if "trend" in t.wants:
            if not cluster:
                return None
            plan.append(("get_demand_trend", {"cluster": cluster, "window": "1y"}))
        return plan or None

    async def _knowledge(self, question: str, cluster: str | None, run, web_fallback: bool = True) -> None:
        args = {"query": question[:200]}
        if cluster:
            args["cluster"] = cluster
        res = await run("search_knowledge", args)
        weak = (not res.usable) or res.data.get("weak", False)
        if weak and web_fallback:
            await run("web_search", {"query": question[:150], "kind": "definition"})

    async def _data_llm(self, question, ctx, budget, out, trace, tier) -> None:
        names = ["get_cluster_demand", "get_top_skills", "get_demand_trend", "search_jobs", "get_job"]
        tools = self.app.registry.schemas(names)
        sys_prompt = ("You choose which data tool to call to answer the student's question. "
                      "Call the tool(s) you need. Do not answer the question yourself.")
        extra: list[dict] = []
        for _ in range(3):
            budget.use_llm(reserve=1)
            prompt = Prompt(system=sys_prompt, question=question, extra=extra)
            comp = await self.app.router.complete(ChatRequest(tier=tier, prompt=prompt, tools=tools, max_tokens=300,
                                                               agent="data", trace_id=trace))
            if not comp.tool_calls:
                return
            extra.append({"role": "assistant", "content": comp.text or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in comp.tool_calls]})
            for tc in comp.tool_calls:
                res = await self.run_tool(tc.name, tc.arguments, ctx, budget, out)
                extra.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": res.to_json(3000)})
            if len(ctx.calls) >= budget.tool_max:
                return

    async def _llm_triage(self, question, triage: Triage, budget, trace, tier_map):
        try:
            budget.use_llm(reserve=1)
            comp = await self.app.router.complete(ChatRequest(
                tier="small", prompt=Prompt(system=load_prompt("triage"), question=question), max_tokens=150,
                agent="triage", trace_id=trace))
        except (BudgetExceeded, AllProvidersFailed):
            triage.needs_llm = False
            if triage.intent == "unclear":
                triage.intent, triage.difficulty = "knowledge", "easy"
            return triage, question
        obj = parse_json(comp.text) or {}
        intent = obj.get("intent")
        if intent in ("data", "knowledge", "curriculum", "advice", "chitchat", "out_of_scope"):
            triage.intent = intent
        elif triage.intent == "unclear":
            triage.intent = "knowledge"
        if obj.get("difficulty") in ("easy", "medium", "hard"):
            triage.difficulty = obj["difficulty"]
        elif triage.difficulty not in ("easy", "medium", "hard"):
            triage.difficulty = "easy"
        if obj.get("cluster") in CLUSTERS and not triage.cluster:
            triage.cluster = obj["cluster"]
        if triage.intent == "data" and not triage.wants:
            triage.wants.add("demand")
        rewrite = obj.get("rewrite")
        if isinstance(rewrite, str) and 3 < len(rewrite) < 400:
            question = rewrite
        triage.needs_llm = False
        return triage, question

    async def _advice(self, question, evidence: Evidence, budget, trace, personal: bool) -> str:
        prompt = Prompt(system=load_prompt("system_core") + "\n\n" + load_prompt("advice"), question=question,
                        blocks=evidence.blocks)
        comp = await self.app.router.complete(ChatRequest(
            tier=self.cfg("difficulty", "advice_tier", default="large"), prompt=prompt, max_tokens=500,
            agent="advice", trace_id=trace, personal=personal))
        return comp.text.strip()

    def _writer_prompt(self, question: str, req: AskInput, evidence: Evidence, advice: str) -> Prompt:
        system = load_prompt("system_core") + "\n\n" + load_prompt("writer", OUTPUT_FORMAT=self.cfg("output_format", default=""))
        blocks = list(evidence.blocks)
        if evidence.notes:
            blocks.insert(0, Block("profile", "System notes (always true, do not contradict):",
                                   rows=[t for _, t in evidence.notes]))
        if advice:
            blocks.append(Block("tool", "Planning notes (internal):", rows=[advice]))
        n = self.cfg("history_turns", default=6)
        history = [{"role": t["role"], "content": t["content"][:1200]} for t in req.turns[-n:]
                   if t.get("role") in ("user", "assistant")]
        return Prompt(system=system, question=question, history=history, blocks=blocks)

    async def _llm_verify(self, answer: str, evidence: Evidence, budget, trace) -> bool:
        """True when the model says the answer has unsupported claims."""
        try:
            budget.use_llm()
        except BudgetExceeded:
            return False
        try:
            body = f"EVIDENCE:\n{evidence.text[:6000]}\n\nANSWER:\n{answer}"
            comp = await self.app.router.complete(ChatRequest(
                tier="small", prompt=Prompt(system=load_prompt("verifier"), question=body), max_tokens=200,
                agent="verifier", trace_id=trace))
        except AllProvidersFailed:
            return False
        obj = parse_json(comp.text) or {}
        return obj.get("ok") is False

    @staticmethod
    def _notes_from(results: list[dict], triage: Triage) -> list[tuple[str, str]]:
        notes: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in results:
            name, res = item["name"], item["result"]
            if name == "search_knowledge":
                continue
            if res.status in ("unavailable", "error"):
                if name == "web_search":
                    msg = "I couldn't check the web right now."
                elif item["source"] == "curriculum":
                    msg = "Your curriculum information is not available right now."
                else:
                    msg = ("I couldn't reach the live job-market data right now, so I am not quoting market numbers "
                           "in this answer.")
                if msg not in seen:
                    seen.add(msg)
                    notes.append((f"{name}_{res.status}", msg))
            elif res.status == "stale":
                msg = f"Market data may be out of date: {res.reason}."
                if msg not in seen:
                    seen.add(msg)
                    notes.append((f"{name}_stale", msg))
            elif res.status == "empty" and item["source"] == "backend":
                notes.append((f"{name}_empty", "No matching market data was found for this request."))
            if name == "get_skill_gaps" and res.usable and res.data.get("demand_status") in ("unavailable", "empty"):
                notes.append(("gaps_no_demand", "Market demand data was not available, so I could not compare "
                                                "your modules with current skill demand."))
        return notes
