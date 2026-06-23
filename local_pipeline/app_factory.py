from __future__ import annotations

from fastapi import FastAPI

from .api_admin import router as admin_router
from .api_chat import router as chat_router
from .config import ensure_runtime_dirs
from .lifespan import lifespan
from .app_state import app as shared_app


def create_app() -> FastAPI:
    """
    Application factory:
    - avoids import-time side effects
    - keeps router wiring in one place
    - allows future dependency injection and testing
    """
    ensure_runtime_dirs()

    app = shared_app
    app.router.lifespan_context = lifespan
    app.include_router(chat_router)
    app.include_router(admin_router)
    return app