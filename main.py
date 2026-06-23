from __future__ import annotations

import uvicorn

from local_pipeline.app_factory import create_app

app = create_app()


def run() -> None:
    uvicorn.run(app, host="0.0.0.0", port=18000)


if __name__ == "__main__":
    run()