from __future__ import annotations

import uvicorn

from .app_state import app

# 匯入路由模組以完成 route registration
from . import api_admin as _api_admin  # noqa: F401,E402
from . import api_chat as _api_chat  # noqa: F401,E402


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=18000)


if __name__ == "__main__":
    run()