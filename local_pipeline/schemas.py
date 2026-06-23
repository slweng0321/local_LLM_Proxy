from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

"""
Shared data structures for the local pipeline.
"""


def _ensure_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _ensure_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _ensure_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _ensure_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class RetrievedFile:
    path: str
    reason: str
    preview: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "RetrievedFile":
        data = payload or {}
        return cls(
            path=_ensure_str(data.get("path")),
            reason=_ensure_str(data.get("reason")),
            preview=_ensure_str(data.get("preview")),
        )


@dataclass(slots=True)
class TaskState:
    task_id: str
    created_at: float
    request_hash: str
    user_request: str
    repo_root: str

    pipeline_mode: str = "agent"
    requested_model: str = ""

    plan_result: dict[str, Any] = field(default_factory=dict)
    repo_manifest: dict[str, Any] = field(default_factory=dict)
    retrieved_files: list[dict[str, Any]] = field(default_factory=list)

    task_plan: dict[str, Any] = field(default_factory=dict)
    file_plan: dict[str, Any] = field(default_factory=dict)

    generated_files: list[dict[str, Any]] = field(default_factory=list)
    critic_report: dict[str, Any] = field(default_factory=dict)
    final_files: list[dict[str, Any]] = field(default_factory=list)

    apply_mode: str = "dry-run"
    apply_result: dict[str, Any] = field(default_factory=dict)

    status: str = "created"
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "TaskState":
        data = payload or {}
        return cls(
            task_id=_ensure_str(data.get("task_id")),
            created_at=_ensure_float(data.get("created_at")),
            request_hash=_ensure_str(data.get("request_hash")),
            user_request=_ensure_str(data.get("user_request")),
            repo_root=_ensure_str(data.get("repo_root")),
            pipeline_mode=_ensure_str(data.get("pipeline_mode"), "agent") or "agent",
            requested_model=_ensure_str(data.get("requested_model")),
            plan_result=_ensure_dict(data.get("plan_result")),
            repo_manifest=_ensure_dict(data.get("repo_manifest")),
            retrieved_files=_ensure_list(data.get("retrieved_files")),
            task_plan=_ensure_dict(data.get("task_plan")),
            file_plan=_ensure_dict(data.get("file_plan")),
            generated_files=_ensure_list(data.get("generated_files")),
            critic_report=_ensure_dict(data.get("critic_report")),
            final_files=_ensure_list(data.get("final_files")),
            apply_mode=_ensure_str(data.get("apply_mode"), "dry-run") or "dry-run",
            apply_result=_ensure_dict(data.get("apply_result")),
            status=_ensure_str(data.get("status"), "created") or "created",
            metrics=_ensure_dict(data.get("metrics")),
        )


__all__ = [
    "RetrievedFile",
    "TaskState",
]