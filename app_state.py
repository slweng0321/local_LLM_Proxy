from __future__ import annotations

from fastapi import FastAPI

"""
Shared application state.

Responsibilities:
- own the single FastAPI app instance for the whole project
- own the shared in-flight request registry used for debounce / duplicate suppression

Non-responsibilities:
- route registration logic
- request handling
- pipeline orchestration
- database access
- model calls
"""

app = FastAPI()
in_flight: dict[str, float] = {}

__all__ = [
    "app",
    "in_flight",
]