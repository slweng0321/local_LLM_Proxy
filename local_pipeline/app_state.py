from __future__ import annotations

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
    Synchronous in-flight registry for duplicate suppression.

    This object deliberately exposes a dict-like API so lifecycle helpers can
    operate on a single, consistent interface without mixing async/sync access
    patterns.
    """
    data: dict[str, float] = field(default_factory=dict)

    def get(self, key: str) -> float | None:
        return self.data.get(key)

    def set(self, key: str, value: float) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def pop(self, key: str, default: float | None = None) -> float | None:
        return self.data.pop(key, default)

    def items(self):
        return self.data.items()

    def snapshot(self) -> dict[str, float]:
        return dict(self.data)


app = FastAPI()
in_flight = InFlightRegistry()

__all__ = [
    "app",
    "in_flight",
    "InFlightRegistry",
]