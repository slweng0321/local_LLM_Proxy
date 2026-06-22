from __future__ import annotations

import json
from typing import Any

import requests
from openai import OpenAI

from .config import OLLAMA_BASE, OPENAI_BASE, REQUEST_TIMEOUT

"""
LLM client helpers for Ollama + OpenAI-compatible calls.

Responsibilities:
- provide a shared OpenAI-compatible client
- call Ollama native keep_alive API
- unload models
- perform one-shot chat calls
- safely parse JSON responses
"""

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=OPENAI_BASE, api_key="ollama")
    return _client


client = get_client()


def native_keep_alive(model: str, keep_alive: int | str) -> dict[str, Any]:
    response = requests.post(
        f"{OLLAMA_BASE}/api/chat",
        json={
            "model": model,
            "messages": [],
            "keep_alive": keep_alive,
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"raw": payload}


def unload_model(model: str) -> None:
    try:
        result = native_keep_alive(model, 0)
        reason = result.get("done_reason", "unknown")
        print(f" 🧹 已請求卸載 {model} (done_reason={reason})")
    except Exception as exc:
        print(f" ⚠️ 卸載 {model} 失敗: {exc}")


def chat_once(
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

    response = get_client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, *messages],
        temperature=temperature,
        extra_body=extra_body,
        timeout=REQUEST_TIMEOUT,
    )
    return response.choices[0].message.content or ""


def load_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return default


__all__ = [
    "client",
    "get_client",
    "native_keep_alive",
    "unload_model",
    "chat_once",
    "load_json",
]