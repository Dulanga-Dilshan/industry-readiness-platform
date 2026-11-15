"""HTTP client for the backend's read-only tool endpoints: retries, circuit breaker, last-good cache."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from advisor.config.settings import (BREAKER_FAILURES, BREAKER_OPEN_S, CACHE_KEEP_DAYS, CONNECT_TIMEOUT_S, READ_TIMEOUT_S,
                                     RETRIES, RETRY_BACKOFF_S, STALE_AFTER_HOURS, TOTAL_BUDGET_S)

from .envelope import ToolResult

log = logging.getLogger("advisor.backend")
Validator = Callable[[dict], str | None]


def parse_as_of(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ResultCache:
    """Last good result per (tool, arguments). Market data is public, so caching it is safe."""

    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time):
        self.clock = clock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS tool_cache(key TEXT PRIMARY KEY, ts REAL, body TEXT)")
        self.db.commit()

    def put(self, key: str, body: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO tool_cache VALUES(?,?,?)", (key, self.clock(), json.dumps(body)))
        self.db.commit()

    def get(self, key: str, keep_days: float) -> dict | None:
        row = self.db.execute("SELECT ts, body FROM tool_cache WHERE key=?", (key,)).fetchone()
        if not row or self.clock() - row[0] > keep_days * 86400:
            return None
        return json.loads(row[1])

    def purge(self, keep_days: float) -> None:
        self.db.execute("DELETE FROM tool_cache WHERE ts<?", (self.clock() - keep_days * 86400,))
        self.db.commit()


class Breaker:
    def __init__(self, failures: int, open_s: float, clock: Callable[[], float]):
        self.limit, self.open_s, self.clock = failures, open_s, clock
        self.fails = 0
        self.opened_at: float | None = None
        self.probing = False

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.open_s and not self.probing:
            self.probing = True          # half-open: exactly one probe call
            return True
        return False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half-open" if self.clock() - self.opened_at >= self.open_s else "open"

    def success(self) -> None:
        self.fails, self.opened_at, self.probing = 0, None, False

    def failure(self) -> None:
        self.fails += 1
        self.probing = False
        if self.fails >= self.limit or self.opened_at is not None:
            self.opened_at = self.clock()


class BackendClient:
    def __init__(self, base_url: str, secret: str, cfg: Callable[[], dict] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, cache: ResultCache | None = None,
                 sleep: Callable[[float], Any] = asyncio.sleep, backoff: tuple[float, ...] = RETRY_BACKOFF_S):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self._cfg = cfg or (lambda: {})
        self.transport = transport
        self.clock, self.wall, self.sleep, self.backoff = clock, wall, sleep, backoff
        self.cache = cache or ResultCache(clock=wall)
        self.breakers: dict[str, Breaker] = {}
        self.alerts: list[str] = []

    def _c(self, key: str, default: Any) -> Any:
        return self._cfg().get(key, default)

    def breaker(self, tool: str) -> Breaker:
        return self.breakers.setdefault(tool, Breaker(BREAKER_FAILURES, BREAKER_OPEN_S, self.clock))

    # ---- main entry ---------------------------------------------------------
    async def get(self, tool: str, path: str, params: dict, user_id: str | None, validate: Validator,
                  has_as_of: bool = True) -> ToolResult:
        key = f"{tool}:{json.dumps(params, sort_keys=True)}"
        br = self.breaker(tool)
        if not br.allow():
            return self._fallback(key, "the data service is temporarily unavailable")
        total = float(self._c("total_budget", TOTAL_BUDGET_S))
        try:
            result = await asyncio.wait_for(self._attempts(tool, path, params, user_id, validate, key, br, has_as_of),
                                            timeout=total)
        except asyncio.TimeoutError:
            br.failure()
            return self._fallback(key, "the data service took too long to answer")
        return result

    async def _attempts(self, tool, path, params, user_id, validate, key, br: Breaker, has_as_of) -> ToolResult:
        retries = int(self._c("retries", RETRIES))
        timeout = httpx.Timeout(float(self._c("read_timeout", READ_TIMEOUT_S)),
                                connect=float(self._c("connect_timeout", CONNECT_TIMEOUT_S)))
        headers = {"X-Service-Secret": self.secret, "Accept": "application/json"}
        if user_id:
            headers["X-User-Id"] = user_id
        last_reason = "the data service did not answer"
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self.transport) as client:
            for attempt in range(retries + 1):
                retry_after = None
                try:
                    resp = await client.get(path, params={k: v for k, v in params.items() if v not in (None, "", [])},
                                            headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_reason = "the data service did not answer in time" if isinstance(e, httpx.TimeoutException) \
                        else "the data service could not be reached"
                else:
                    code = resp.status_code
                    if code in (401, 403):
                        msg = f"backend rejected the service credentials for {tool} ({code})"
                        log.error("CONFIG ALERT: %s", msg)
                        self.alerts.append(msg)
                        return ToolResult.error("data service credentials were rejected")
                    if code in (404, 501):
                        return ToolResult.unavailable("this data is not available yet")
                    if code == 429 or code >= 500:
                        last_reason = "the data service is busy or failing"
                        ra = resp.headers.get("retry-after")
                        try:
                            retry_after = float(ra) if ra else None
                        except ValueError:
                            retry_after = None
                    elif code >= 400:
                        return ToolResult.error(f"unexpected response {code} from the data service")
                    else:
                        return self._handle_ok(resp, validate, key, br, has_as_of)
                if attempt < retries:
                    delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                    if retry_after is not None:
                        delay = max(delay, min(retry_after, 2.0))
                    await self.sleep(delay)
        br.failure()
        return self._fallback(key, last_reason)

    def _handle_ok(self, resp: httpx.Response, validate: Validator, key: str, br: Breaker, has_as_of: bool) -> ToolResult:
        try:
            body = resp.json()
        except ValueError:
            return ToolResult.error("the data service returned an unreadable response")
        if not isinstance(body, dict):
            return ToolResult.error("the data service returned an unexpected response")
        problem = validate(body)
        if problem:
            log.warning("schema mismatch: %s", problem)
            return ToolResult.error("the data service returned data in an unexpected format")
        br.success()
        self.cache.put(key, body)
        return self._to_result(body, has_as_of)

    def _to_result(self, body: dict, has_as_of: bool, served_from_cache: bool = False) -> ToolResult:
        as_of_raw = body.get("as_of")
        data = {k: v for k, v in body.items() if k not in ("as_of", "mock")}
        main = next((v for v in data.values() if isinstance(v, list)), None)
        if main is not None and len(main) == 0:
            return ToolResult(status="empty", reason="no data for this request", as_of=as_of_raw, data=data)
        stale_after = float(self._c("stale_after_hours", STALE_AFTER_HOURS))
        as_of = parse_as_of(as_of_raw)
        if has_as_of and as_of is None:
            return ToolResult.error("the data service did not say how recent its data is")
        if as_of is not None:
            age_h = (datetime.now(timezone.utc) - as_of).total_seconds() / 3600
            if age_h > stale_after:
                return ToolResult("stale", f"data was last updated on {as_of.date().isoformat()}", as_of_raw, data)
        return ToolResult.ok(data, as_of_raw)

    def _fallback(self, key: str, reason: str) -> ToolResult:
        """Backend is down: serve the last good copy as stale, with its date."""
        body = self.cache.get(key, float(self._c("cache_keep_days", CACHE_KEEP_DAYS)))
        if body is None:
            return ToolResult.unavailable(reason)
        res = self._to_result(body, has_as_of=False, served_from_cache=True)
        as_of = parse_as_of(body.get("as_of"))
        when = as_of.date().isoformat() if as_of else "an earlier date"
        return ToolResult("stale", f"{reason}; showing the last saved result from {when}", body.get("as_of"), res.data)
