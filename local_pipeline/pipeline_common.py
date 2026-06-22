from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from .config import DUPLICATE_WINDOW_SECONDS
from .schemas import TaskState

"""
Helpers for pipeline bootstrap and request lifecycle management.

This module should stay narrowly focused on:
- request hashing
- duplicate in-flight detection
- in-flight bookkeeping
- TaskState creation

Do not place LLM parsing, repository logic, patch logic, or DB I/O here.
"""


def build_request_hash(
    *,
    latest_user_content: str,
    workspace_root: str,
    pipeline_mode: str,
) -> str:
    payload = {
        "content": latest_user_content,
        "root": workspace_root,
        "mode": pipeline_mode,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def is_duplicate_in_flight(
    in_flight: dict[str, float],
    request_hash: str,
    *,
    now: float | None = None,
    window_seconds: int = DUPLICATE_WINDOW_SECONDS,
) -> bool:
    current = time.perf_counter() if now is None else now
    last_seen = in_flight.get(request_hash)
    return last_seen is not None and (current - last_seen) < window_seconds


def mark_in_flight(
    in_flight: dict[str, float],
    request_hash: str,
    *,
    now: float | None = None,
) -> float:
    marked_at = time.perf_counter() if now is None else now
    in_flight[request_hash] = marked_at
    return marked_at


def clear_in_flight(in_flight: dict[str, float], request_hash: str) -> None:
    in_flight.pop(request_hash, None)


def cleanup_in_flight(
    in_flight: dict[str, float],
    *,
    now: float | None = None,
    window_seconds: int = DUPLICATE_WINDOW_SECONDS,
) -> None:
    current = time.perf_counter() if now is None else now
    expired = [
        req_hash
        for req_hash, started_at in list(in_flight.items())
        if (current - started_at) >= window_seconds
    ]
    for req_hash in expired:
        in_flight.pop(req_hash, None)


def create_task_state(
    *,
    request_hash: str,
    user_request: str,
    repo_root: str,
    pipeline_mode: str,
    requested_model: str,
    apply_mode: str,
) -> TaskState:
    return TaskState(
        task_id=str(uuid.uuid4()),
        created_at=time.time(),
        request_hash=request_hash,
        user_request=user_request,
        repo_root=repo_root,
        pipeline_mode=pipeline_mode,
        requested_model=requested_model,
        apply_mode=apply_mode,
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def coerce_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    out: list[str] = []
    for item in values:
        text = normalize_text(item)
        if text:
            out.append(text)
    return out


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)

    return out


def merge_path_lists(*path_groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in path_groups:
        merged.extend(coerce_str_list(group))
    return unique_preserve_order(merged)


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


__all__ = [
    "build_request_hash",
    "is_duplicate_in_flight",
    "mark_in_flight",
    "clear_in_flight",
    "cleanup_in_flight",
    "create_task_state",
    "normalize_text",
    "coerce_str_list",
    "unique_preserve_order",
    "merge_path_lists",
    "ensure_dict",
]