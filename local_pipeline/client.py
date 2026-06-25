from __future__ import annotations

import json
import asyncio
from typing import Any

import aiohttp
from openai import AsyncOpenAI

from .config import OLLAMA_BASE, OPENAI_BASE, REQUEST_TIMEOUT

"""
Async LLM client helpers for Ollama + OpenAI-compatible calls.
"""

_client: AsyncOpenAI | None = None
_session: aiohttp.ClientSession | None = None
_client_lock = asyncio.Lock()
_session_lock = asyncio.Lock()


async def get_client() -> AsyncOpenAI:
    global _client
    async with _client_lock:
        if _client is None:
            _client = AsyncOpenAI(base_url=OPENAI_BASE, api_key="ollama")
        return _client


async def get_session() -> aiohttp.ClientSession:
    global _session
    async with _session_lock:
        if _session is None or _session.closed:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            connector = aiohttp.TCPConnector(
                limit=100,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                keepalive_timeout=30,
            )
            _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _session


async def close_clients() -> None:
    global _session, _client
    async with _session_lock:
        if _session is not None and not _session.closed:
            await _session.close()
        _session = None
    async with _client_lock:
        _client = None


async def native_keep_alive(model: str, keep_alive: int | str) -> dict[str, Any]:
    session = await get_session()
    async with session.post(
        f"{OLLAMA_BASE}/api/chat",
        json={
            "model": model,
            "messages": [],
            "keep_alive": keep_alive,
        },
        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
    ) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
        return payload if isinstance(payload, dict) else {"raw": payload}


async def unload_model(model: str) -> None:
    try:
        result = await native_keep_alive(model, 0)
    except Exception as exc:
        print(f" ⚠️ 卸載 {model} 失敗: {exc}")


async def chat_once(
    model: str,
    system: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
) -> str:
    extra_body: dict[str, Any] = {"options": options or {}}
    if think is not None:
        extra_body["think"] = think

    client = await get_client()
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, *messages],
        temperature=temperature,
        extra_body=extra_body,
        timeout=REQUEST_TIMEOUT,
    )
    if not getattr(response, "choices", None):
        return "{}"

    first_choice = response.choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", None)
    return content or "{}"


async def chat_stream(
    model: str,
    system: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
):
    extra_body: dict[str, Any] = {"options": options or {}}
    if think is not None:
        extra_body["think"] = think

    client = await get_client()
    stream = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, *messages],
        temperature=temperature,
        extra_body=extra_body,
        timeout=REQUEST_TIMEOUT,
        stream=True,
    )

    yielded = False
    async for chunk in stream:
        yielded = True
        yield chunk

    if not yielded:
        return


def load_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return default


__all__ = [
    "get_client",
    "get_session",
    "close_clients",
    "native_keep_alive",
    "unload_model",
    "chat_once",
    "chat_stream",
    "load_json",
]