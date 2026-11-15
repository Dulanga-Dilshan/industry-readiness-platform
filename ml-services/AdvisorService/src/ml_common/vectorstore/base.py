"""VectorStore interface. Filters are simple equality dicts, e.g. {"user_id": "u1"}."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Point:
    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hit:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStore(ABC):
    @abstractmethod
    async def ensure_collection(self, name: str, dim: int) -> None: ...

    @abstractmethod
    async def upsert(self, name: str, points: list[Point]) -> None: ...

    @abstractmethod
    async def search(self, name: str, vector: list[float], limit: int = 5,
                     filter: dict[str, Any] | None = None) -> list[Hit]: ...

    @abstractmethod
    async def delete(self, name: str, filter: dict[str, Any]) -> None: ...

    @abstractmethod
    async def scroll(self, name: str, filter: dict[str, Any] | None = None, limit: int = 10000) -> list[Hit]: ...

    @abstractmethod
    async def count(self, name: str, filter: dict[str, Any] | None = None) -> int: ...

    @abstractmethod
    async def drop(self, name: str) -> None: ...

    @abstractmethod
    async def exists(self, name: str) -> bool: ...

    @abstractmethod
    async def get_meta(self, name: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def set_meta(self, name: str, meta: dict[str, Any]) -> None: ...
