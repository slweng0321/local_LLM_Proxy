from __future__ import annotations

import json
import time

"""
Small logging helpers for pipeline stage timing.

Responsibilities:
- print stage labels consistently
- measure elapsed seconds between stages
- stay dependency-free

Non-responsibilities:
- no logging framework setup
- no file logging
- no FastAPI / database / pipeline logic
"""


def log_step(label: str, elapsed: float | None = None) -> float:
    if elapsed is not None:
        print(f" ⏱ 耗時 {elapsed:.2f}s")
    print(f"\n{label}")
    return time.perf_counter()


def log_stage_start(kind: str, model: str, message: str) -> float:
    print(f"{kind} [{model}] {message}")
    return time.perf_counter()


def log_stage_done(kind: str, model: str, started_at: float) -> None:
    print(f"✅ [{kind} · {model}] 完成")
    print(f" ⏱ 耗時 {time.perf_counter() - started_at:.2f}s")


def log_stage_unload(model: str) -> None:
    print(f" 🧹 已請求卸載 {model} (done_reason=unload)")


def log_ttft(started_at: float) -> None:
    print(f" ⚡ TTFT: {time.perf_counter() - started_at:.2f}s")


def log_heartbeat(message: str) -> str:
    print(f" ⚙️ [Pipeline] {message}")
    payload = {
        "id": f"chatcmpl-heartbeat-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "local",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": f"⚙️ [Pipeline] {message}",
                },
                "finish_reason": None,
            }
        ],
        "event": "progress",
    }
    return json.dumps(payload, ensure_ascii=False)


def log_elapsed(label: str, started_at: float) -> float:
    elapsed = time.perf_counter() - started_at
    print(f" ⏱ {label}: {elapsed:.2f}s")
    return elapsed


def elapsed_since(started_at: float) -> float:
    return time.perf_counter() - started_at


def _log(label: str, elapsed: float | None = None) -> float:
    return log_step(label, elapsed)


__all__ = [
    "log_step",
    "log_stage_start",
    "log_stage_done",
    "log_stage_unload",
    "log_ttft",
    "log_heartbeat",
    "log_elapsed",
    "elapsed_since",
]