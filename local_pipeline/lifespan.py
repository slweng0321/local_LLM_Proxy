from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .client import close_clients
from .config import ensure_runtime_dirs


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan hook.

    適合放：
    - runtime dir initialization
    - async client initialization
    - cache warmup
    - metrics/exporter startup
    """
    ensure_runtime_dirs()
    try:
        yield
    finally:
        # 放未來的 async cleanup，例如 http client close / background task shutdown
        await close_clients()