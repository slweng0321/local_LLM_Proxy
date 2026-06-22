from __future__ import annotations

import uvicorn

from local_pipeline.api_admin import router as admin_router
from local_pipeline.api_chat import router as chat_router
from local_pipeline.app_state import app

app.include_router(chat_router)
app.include_router(admin_router)


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=18000)


if __name__ == "__main__":
    run()