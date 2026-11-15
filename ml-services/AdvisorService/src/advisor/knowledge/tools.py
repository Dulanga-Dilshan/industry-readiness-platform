"""Tools: search_knowledge and search_user_memory."""
from __future__ import annotations

from advisor.tools.base import ToolContext
from advisor.tools.envelope import ToolResult

from .bootstrap import IndexMismatchError, KnowledgeIndex


async def search_knowledge(index: KnowledgeIndex, args: dict) -> ToolResult:
    if not index.ready:
        reason = "the knowledge base is still being prepared" if index.state == "building" else \
            f"the knowledge base is unavailable ({index.error or 'index failed'})"
        return ToolResult.unavailable(reason)
    try:
        hits = await index.search(args["query"], args.get("cluster"), args.get("type"), limit=4)
    except IndexMismatchError as e:
        return ToolResult.unavailable(f"knowledge index must be rebuilt: {e}")
    if not hits:
        return ToolResult.empty("nothing relevant in the knowledge base")
    weak = index.is_weak(hits)
    shown = [] if weak else hits[:3]      # weak hits are hidden: the caller falls back to the web
    return ToolResult.ok({"weak": weak, "hits": [
        {"id": h.id, "score": round(h.score, 4), "vector_score": round(h.vector_score, 3),
         "title": h.payload["title"], "heading": h.payload["heading"], "ref": h.payload["ref"],
         "text": h.payload["text"][:1500]} for h in shown]})


async def search_user_memory(memory, args: dict, ctx: ToolContext) -> ToolResult:
    """The user id comes from the server-side context, never from model arguments."""
    if not ctx.user_id:
        return ToolResult.empty("no user")
    if (ctx.profile or {}).get("consent", {}).get("store_memory") is False:
        return ToolResult.empty("the student did not allow memory")
    hits = await memory.search(ctx.user_id, args["query"], 3)
    return ToolResult.ok({"hits": hits}) if hits else ToolResult.empty("no relevant memories")
