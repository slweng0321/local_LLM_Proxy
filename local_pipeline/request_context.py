from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import (
    AGENT_MODEL_ALIASES,
    DEFAULT_APPLY_MODE,
    PLAN_MODEL_ALIASES,
    VALID_APPLY_MODES,
    WORKSPACE_ROOT,
)

"""
Request-context helpers.
"""


def resolve_pipeline_mode(body: dict[str, Any]) -> str:
    requested_model = str(body.get("model", "")).strip().lower()

    if requested_model in PLAN_MODEL_ALIASES:
        return "plan"
    if requested_model in AGENT_MODEL_ALIASES:
        return "agent"
    return "agent"


def normalize_apply_mode(body: dict[str, Any]) -> str:
    extra = body.get("extra_body") or {}
    raw_apply_mode = (
        extra.get("apply_mode")
        or body.get("apply_mode")
        or DEFAULT_APPLY_MODE
    )
    apply_mode = str(raw_apply_mode).strip().lower()
    return apply_mode if apply_mode in VALID_APPLY_MODES else "dry-run"


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()

    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


@lru_cache(maxsize=16)
def _cached_git_root(path_str: str) -> str | None:
    root = find_git_root(Path(path_str))
    return root.as_posix() if root is not None else None


def _coerce_existing_path(value: Any) -> Path | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    candidate = Path(text).expanduser().resolve()
    return candidate if candidate.exists() else None


def _extract_workspace_hint_from_messages(messages: list[dict[str, Any]]) -> Path | None:
    for msg in reversed(messages or []):
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped.startswith("WORKSPACE:"):
                continue

            hint = stripped[len("WORKSPACE:") :].strip()
            candidate = _coerce_existing_path(hint)
            if candidate is not None:
                return candidate

    return None


def resolve_workspace_root(body: dict[str, Any]) -> Path:
    extra = body.get("extra_body") or {}
    override = extra.get("workspace_root") or body.get("workspace_root")

    override_path = _coerce_existing_path(override)
    if override_path is not None:
        return override_path

    messages = body.get("messages", []) or []
    hinted_path = _extract_workspace_hint_from_messages(messages)
    if hinted_path is not None:
        return hinted_path

    cached_git = _cached_git_root(WORKSPACE_ROOT.as_posix())
    if cached_git is not None:
        return Path(cached_git)

    return WORKSPACE_ROOT


def extract_latest_user_content(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue

        content = msg.get("content", "")

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue

                text = item.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)

            return "\n".join(text_parts)

        return str(content)

    return ""


def build_request_hash(
    *,
    latest_user_content: str,
    effective_root: Path,
    pipeline_mode: str,
    apply_mode: str,
    requested_model: str,
) -> str:
    payload = {
        "content": latest_user_content,
        "root": effective_root.as_posix(),
        "mode": pipeline_mode,
        "apply_mode": apply_mode,
        "model": requested_model,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_request_context(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages", []) or []
    pipeline_mode = resolve_pipeline_mode(body)
    apply_mode = normalize_apply_mode(body)
    effective_root = resolve_workspace_root(body)
    latest_user_content = extract_latest_user_content(messages)
    requested_model = str(body.get("model", "")).strip()

    request_hash = build_request_hash(
        latest_user_content=latest_user_content,
        effective_root=effective_root,
        pipeline_mode=pipeline_mode,
        apply_mode=apply_mode,
        requested_model=requested_model,
    )

    return {
        "messages": messages,
        "pipeline_mode": pipeline_mode,
        "apply_mode": apply_mode,
        "effective_root": effective_root,
        "latest_user_content": latest_user_content,
        "request_hash": request_hash,
        "requested_model": requested_model,
    }


__all__ = [
    "resolve_pipeline_mode",
    "normalize_apply_mode",
    "find_git_root",
    "resolve_workspace_root",
    "extract_latest_user_content",
    "build_request_hash",
    "build_request_context",
]