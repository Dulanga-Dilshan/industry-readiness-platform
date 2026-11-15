"""SQLite ledger of every model call, aggregated per minute/day/month."""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Callable


class QuotaLedger:
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("""CREATE TABLE IF NOT EXISTS calls(
            ts REAL, provider TEXT, model TEXT, agent TEXT, trace TEXT,
            tokens_in INTEGER, tokens_out INTEGER, latency_ms REAL, status TEXT)""")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_calls_ts ON calls(ts)")
        self._db.commit()

    def record(self, provider: str, model: str, agent: str, tokens_in: int, tokens_out: int,
               latency_ms: float, status: str, trace: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)",
                             (self.clock(), provider, model, agent, trace, tokens_in, tokens_out, latency_ms, status))
            self._db.commit()

    def window(self, provider: str, seconds: float, ok_only: bool = False) -> dict:
        since = self.clock() - seconds
        q = "SELECT COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0) FROM calls WHERE provider=? AND ts>=?"
        if ok_only:
            q += " AND status='ok'"
        with self._lock:
            n, tok = self._db.execute(q, (provider, since)).fetchone()
        return {"requests": n, "tokens": tok}

    def tokens_last_minute(self, provider: str) -> int:
        return self.window(provider, 60)["tokens"]

    def aggregate(self, since_seconds: float | None = None) -> list[dict]:
        since = self.clock() - since_seconds if since_seconds else 0
        with self._lock:
            rows = self._db.execute(
                """SELECT provider, agent, COUNT(*), SUM(tokens_in), SUM(tokens_out),
                          SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END), AVG(latency_ms)
                   FROM calls WHERE ts>=? GROUP BY provider, agent ORDER BY provider, agent""", (since,)).fetchall()
        return [{"provider": r[0], "agent": r[1], "requests": r[2], "tokens_in": r[3] or 0,
                 "tokens_out": r[4] or 0, "errors": r[5] or 0, "avg_latency_ms": round(r[6] or 0, 1)} for r in rows]

    def per_period(self, provider: str) -> dict:
        return {"minute": self.window(provider, 60), "day": self.window(provider, 86400),
                "month": self.window(provider, 30 * 86400)}

    def avg_tokens_per_answer(self, provider: str | None = None) -> float | None:
        """Measured average tokens per answer (grouped by trace id). None when nothing measured."""
        q = "SELECT SUM(tokens_in+tokens_out) FROM calls WHERE status='ok' AND trace!=''"
        args: tuple = ()
        if provider:
            q += " AND provider=?"
            args = (provider,)
        q += " GROUP BY trace"
        with self._lock:
            rows = [r[0] for r in self._db.execute(q, args).fetchall() if r[0]]
        return sum(rows) / len(rows) if rows else None

    def reroute_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM calls WHERE status!='ok'").fetchone()[0]
