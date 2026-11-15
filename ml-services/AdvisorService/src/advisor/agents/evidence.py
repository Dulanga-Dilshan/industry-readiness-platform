"""Turns tool results into prompt blocks, source records and the evidence text used by the verifier."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from advisor.safety.guardrails import wrap_untrusted
from ml_common.providers.budget import Block


@dataclass
class Evidence:
    blocks: list[Block] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)     # (code, visible text)
    text: str = ""                                                  # everything the verifier may accept numbers from

    def add_source(self, kind: str, title: str, ref: str = "", as_of: str | None = None) -> str:
        sid = f"S{len(self.sources) + 1}"
        self.sources.append({"id": sid, "kind": kind, "title": title, "ref": ref, "as_of": as_of})
        return sid

    @property
    def markers(self) -> set[str]:
        return {s["id"] for s in self.sources}


def _rows(data) -> list[str]:
    """One row per list item so token budgeting can drop rows from the end."""
    if isinstance(data, dict):
        list_keys = [k for k, v in data.items() if isinstance(v, list) and v]
        if list_keys:
            head = {k: v for k, v in data.items() if k not in list_keys}
            rows = [json.dumps(head, ensure_ascii=False)] if head else []
            for k in list_keys:
                for it in data[k]:
                    if isinstance(it, dict) and isinstance(it.get("description"), str):
                        it = {**it, "description": wrap_untrusted("job", it.get("key", ""), it["description"])}
                    rows.append(json.dumps({k: it}, ensure_ascii=False))
            return rows
        return [json.dumps(data, ensure_ascii=False)]
    return [json.dumps(data, ensure_ascii=False)]


def build_evidence(results: list[dict], profile_ctx: list[str], personal_rows: list[str]) -> Evidence:
    ev = Evidence()
    if profile_ctx:
        ev.blocks.append(Block("profile", "Student context:", rows=list(profile_ctx)))
        ev.text += "\n".join(profile_ctx) + "\n"
    if personal_rows:
        ev.blocks.append(Block("profile", "What the student has told us before (personal, do not repeat verbatim):",
                               rows=list(personal_rows)))
        ev.text += "\n".join(personal_rows) + "\n"
    for item in results:
        name, res, src = item["name"], item["result"], item["source"]
        args = json.dumps(item["args"], ensure_ascii=False)
        ev.text += f"{name} {args}\n"
        if name == "search_knowledge" and res.usable:
            for hit in res.data.get("hits", []):
                sid = ev.add_source("knowledge", hit["title"], hit["ref"])
                ev.blocks.append(Block("chunk", f"[{sid}] {hit['title']} — {hit['heading']}",
                                       wrap_untrusted("knowledge", hit["ref"], hit["text"]), score=hit["score"]))
                ev.text += hit["text"] + "\n"
            continue
        if name == "web_search" and res.usable:
            for r in res.data.get("results", []):
                sid = ev.add_source("web", r["title"], r["url"])
                body = f"{r['title']}: {r['snippet']}"
                ev.blocks.append(Block("chunk", f"[{sid}] web: {r['url']}",
                                       wrap_untrusted("web", r["url"], body), score=0.3))
                ev.text += body + "\n"
            continue
        if res.usable and res.data is not None and name not in ("search_user_memory",):
            kind = "curriculum" if src == "curriculum" else "backend"
            sid = ev.add_source(kind, name, "", res.as_of)
            label = f"[{sid}] {name}" + (f" (data as of {res.as_of})" if res.as_of else "")
            ev.blocks.append(Block("tool", label, rows=_rows(res.data)))
            ev.text += json.dumps(res.data, ensure_ascii=False, default=str) + "\n"
    return ev
