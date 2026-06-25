from __future__ import annotations

import asyncio
import json
import inspect
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi.responses import StreamingResponse


def sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


def openai_chat_completion_chunk(
    *,
    chunk_id: str,
    model: str,
    content: str = "",
    role: str | None = None,
    finish_reason: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ],
    }
    delta: dict[str, object] = payload["choices"][0]["delta"]  # type: ignore[index]
    if role is not None:
        delta["role"] = role
    if content:
        delta["content"] = content
    return json.dumps(payload, ensure_ascii=False)


def format_chunk(
    content: str,
    finish_reason: str | None = None,
    *,
    chunk_id: str = "chatcmpl-pipeline",
    model: str = "pipeline-monitor",
) -> str:
    return openai_chat_completion_chunk(
        chunk_id=chunk_id,
        model=model,
        role="assistant",
        content=content,
        finish_reason=finish_reason,
    )


def openai_chat_completion_final(
    *,
    chunk_id: str,
    model: str,
    content: str,
    finish_reason: str = "stop",
) -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": finish_reason,
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def openai_stream_response(
    iterator: AsyncIterator[str],
    *,
    on_close: Callable[[], Awaitable[None] | None] | None = None,
    chunk_id: str | None = None,
    model: str = "local",
) -> StreamingResponse:
    completion_id = chunk_id or f"chatcmpl-{uuid.uuid4().hex}"

    async def generator():
        sent_any_chunk = False
        finished = False
        try:
            async for chunk in iterator:
                if not chunk:
                    continue
                stripped = chunk.strip()
                if stripped in {"[DONE]", "data: [DONE]"}:
                    finished = True
                    break
                if stripped.startswith("data:"):
                    yield f"{stripped}\n\n"
                    sent_any_chunk = True
                    continue
                yield f"data: {chunk}\n\n"
                sent_any_chunk = True
            finished = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield f"data: {format_chunk(f'\n> ❌ **Pipeline 錯誤**: {exc}\n\n', chunk_id=completion_id, model=model)}\n\n"
            finished = True
        finally:
            if on_close is not None:
                result = on_close()
                if inspect.isawaitable(result):
                    await result

        if finished:
            if not sent_any_chunk:
                yield f"data: {openai_chat_completion_chunk(chunk_id=completion_id, model=model, role='assistant')}\n\n"
            yield f"data: {openai_chat_completion_chunk(chunk_id=completion_id, model=model, role='assistant', finish_reason='stop')}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=sse_headers(),
    )




def openai_json_response(content: str, *, chunk_id: str | None = None, model: str = "local") -> dict[str, object]:
    completion_id = chunk_id or f"chatcmpl-{uuid.uuid4().hex}"
    return json.loads(openai_chat_completion_final(chunk_id=completion_id, model=model, content=content))


def openai_error_response(message: str, *, code: str = "pipeline_error") -> StreamingResponse:
    async def generator():
        payload = openai_chat_completion_chunk(
            chunk_id=f"chatcmpl-{uuid.uuid4().hex}",
            model="local",
            role="assistant",
            content=message,
        )
        yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers=sse_headers(),
        status_code=200,
    )