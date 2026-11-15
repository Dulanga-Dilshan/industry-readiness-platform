"""Tool registry: strict argument validation, result size caps, loop detection, call limits."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .envelope import ToolResult

MAX_RESULT_CHARS = 6000
MAX_LIST_ROWS = 25


@dataclass
class ToolContext:
    user_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    profile: dict | None = None
    max_tool_calls: int = 6
    calls: list[tuple[str, str]] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)     # {name, args, result} kept for evidence and verification


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict, ToolContext], Awaitable[ToolResult]]
    source: str = "backend"      # backend | knowledge | curriculum | web | memory

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def validate_args(schema: dict, args: Any) -> tuple[dict, str | None]:
    """Strict validator for the small JSON-schema subset we use. Unknown arguments are rejected,
    so a model can never smuggle in a user id."""
    if not isinstance(args, dict):
        return {}, "arguments must be an object"
    props = schema.get("properties", {})
    extra = set(args) - set(props)
    if extra:
        return {}, f"unknown argument(s): {sorted(extra)}"
    for req in schema.get("required", []):
        if req not in args:
            return {}, f"missing required argument '{req}'"
    clean: dict = {}
    for k, v in args.items():
        spec = props[k]
        t = spec.get("type")
        if t == "string":
            if not isinstance(v, str):
                return {}, f"'{k}' must be a string"
            if len(v) > spec.get("maxLength", 300):
                return {}, f"'{k}' is too long"
            if "enum" in spec and v not in spec["enum"]:
                return {}, f"'{k}' must be one of {spec['enum']}"
        elif t == "integer":
            if isinstance(v, bool) or not isinstance(v, int):
                return {}, f"'{k}' must be an integer"
            if v < spec.get("minimum", -10**9) or v > spec.get("maximum", 10**9):
                return {}, f"'{k}' must be between {spec.get('minimum')} and {spec.get('maximum')}"
        elif t == "array":
            if not isinstance(v, list) or len(v) > spec.get("maxItems", 20) or not all(isinstance(i, str) for i in v):
                return {}, f"'{k}' must be a short list of strings"
        clean[k] = v
    return clean, None


def limit_result(r: ToolResult, max_rows: int = MAX_LIST_ROWS) -> ToolResult:
    """Cap lists inside data so one tool result cannot flood the prompt."""
    def cap(o: Any) -> Any:
        if isinstance(o, list):
            return [cap(i) for i in o[:max_rows]]
        if isinstance(o, dict):
            return {k: cap(v) for k, v in o.items()}
        if isinstance(o, str) and len(o) > 3000:
            return o[:3000] + "…"
        return o
    return ToolResult(r.status, r.reason, r.as_of, cap(r.data))


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def schemas(self, names: list[str] | None = None) -> list[dict]:
        return [t.schema() for n, t in self.tools.items() if names is None or n in names]

    async def execute(self, name: str, args: Any, ctx: ToolContext) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult.error(f"unknown tool '{name}'")
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if len(ctx.calls) >= ctx.max_tool_calls:
            return ToolResult.error("tool call limit reached for this question")
        if key in ctx.calls:
            return ToolResult.error("this exact tool call was already made (loop detected)")
        ctx.calls.append(key)
        clean, problem = validate_args(tool.parameters, args)
        if problem:
            return ToolResult.error(problem)
        try:
            result = limit_result(await tool.fn(clean, ctx))
        except Exception:     # the model never sees raw exceptions
            return ToolResult.error("tool failed unexpectedly")
        ctx.results.append({"name": name, "args": clean, "source": tool.source, "result": result})
        return result
