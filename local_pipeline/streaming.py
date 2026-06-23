from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi.responses import StreamingResponse


def sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def sse_response(
    iterator: AsyncIterator[str],
    *,
    on_close: Callable[[], Awaitable[None] | None] | None = None,
) -> StreamingResponse:
    async def generator():
        cancelled = False
        try:
            async for chunk in iterator:
                if chunk:
                    yield f"data: {chunk}\n\n"
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if on_close is not None:
                result = on_close()
                if inspect.isawaitable(result):
                    await result
            if not cancelled:
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=sse_headers(),
    )