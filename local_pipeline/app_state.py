from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import FastAPI

"""
Shared application state.

Responsibilities:
- own the single FastAPI app instance for the whole project
- own shared concurrent runtime state
"""


@dataclass(slots=True)
class InFlightRegistry:
    """
    Async-safe in-flight registry for duplicate suppression.
    """
    data: dict[str, float] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get(self, key: str) -> float | None:
        async with self.lock:
            return self.data.get(key)

    async def set(self, key: str, value: float) -> None:
        async with self.lock:
            self.data[key] = value

    async def delete(self, key: str) -> None:
        async with self.lock:
            self.data.pop(key, None)

    async def pop(self, key: str, default: float | None = None) -> float | None:
        async with self.lock:
            return self.data.pop(key, default)

    async def snapshot(self) -> dict[str, float]:
        async with self.lock:
            return dict(self.data)


app = FastAPI()
in_flight = InFlightRegistry()

__all__ = [
    "app",
    "in_flight",
    "InFlightRegistry",
]