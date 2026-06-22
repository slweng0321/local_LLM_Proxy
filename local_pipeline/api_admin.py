from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .client import unload_model
from .config import (
    AGENT_MODEL_ALIASES,
    AUTO_UNLOAD_AFTER_STAGE,
    CODER_MODEL,
    CRITIC_MODEL,
    DEFAULT_APPLY_MODE,
    ENABLE_AFFECTED_GRAPH_EXPANSION,
    ENABLE_PRE_APPLY_CHECK,
    ENABLE_PROJECT_RUNNERS,
    FILE_PLANNER_MODEL,
    PLAN_MODEL_ALIASES,
    PLAN_REVIEWER_MODEL,
    REVIEWER_MODEL,
    TASK_PLANNER_MODEL,
    WORKSPACE_ROOT,
)
from .db import load_task_payload, update_task_payload
from .patches import apply_patches, rollback_task

router = APIRouter()


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _has_apply_errors(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("errors"):
        return True
    precheck = result.get("precheck")
    if isinstance(precheck, dict) and not precheck.get("ok", True):
        return True
    return False


def _models_payload() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "plan",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "display_name": "Local Pipeline · Plan (唯讀分析)",
                "description": "TaskPlanner → FilePlanner → PlanReviewer（不寫入任何檔案）",
            },
            {
                "id": "agent",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "display_name": "Local Pipeline · Agent (Coder → Critic → Reviewer)",
                "description": "完整四階段 pipeline：TaskPlanner → FilePlanner → Coder → Critic → Reviewer",
            },
            {
                "id": "multi-agent",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "display_name": "Local Pipeline · Agent (legacy alias)",
                "description": "等同於 agent 模式，保留此 ID 以相容舊版 Continue 設定",
            },
        ],
    }


@router.get("/v1/models")
async def list_models() -> dict[str, Any]:
    return _models_payload()


@router.post("/admin/unload")
async def admin_unload(request: Request) -> JSONResponse | dict[str, Any]:
    body = await request.json()
    model = str(body.get("model", "")).strip()
    if not model:
        return _error("model is required", 400)

    unload_model(model)
    return {"ok": True, "model": model}


@router.post("/admin/apply")
async def admin_apply(request: Request) -> JSONResponse | dict[str, Any]:
    body = await request.json()
    task_id = str(body.get("task_id", "")).strip()
    if not task_id:
        return _error("task_id is required", 400)

    payload = load_task_payload(task_id)
    if not payload:
        return _error(f"task {task_id} not found", 404)

    final_files = payload.get("final_files", [])
    if not isinstance(final_files, list) or not final_files:
        return _error(f"task {task_id} has no final_files to apply", 400)

    repo_root = Path(payload.get("repo_root", ".")).resolve()
    result = apply_patches(repo_root, task_id, final_files, apply_mode="apply")

    payload["apply_mode"] = "apply"
    payload["apply_result"] = result
    payload["status"] = "apply_failed" if _has_apply_errors(result) else "applied"
    update_task_payload(task_id, payload)

    if _has_apply_errors(result):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/admin/rollback")
async def admin_rollback(request: Request) -> JSONResponse | dict[str, Any]:
    body = await request.json()
    task_id = str(body.get("task_id", "")).strip()
    if not task_id:
        return _error("task_id is required", 400)

    payload = load_task_payload(task_id)
    repo_root = Path(payload.get("repo_root", ".")).resolve() if payload else WORKSPACE_ROOT

    result = rollback_task(task_id, repo_root)

    if payload:
        payload["status"] = "rollback_failed" if result.get("errors") else "rolled_back"
        payload["rollback_result"] = result
        update_task_payload(task_id, payload)

    if result.get("errors"):
        return JSONResponse(result, status_code=400)
    return result


@router.get("/admin/task/{task_id}")
async def admin_task_status(task_id: str) -> JSONResponse | dict[str, Any]:
    payload = load_task_payload(task_id)
    if not payload:
        return _error(f"task {task_id} not found", 404)
    return payload


@router.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "pipeline": {
            "plan": {
                "task_planner": TASK_PLANNER_MODEL,
                "file_planner": FILE_PLANNER_MODEL,
                "plan_reviewer": PLAN_REVIEWER_MODEL,
            },
            "agent": {
                "task_planner": TASK_PLANNER_MODEL,
                "file_planner": FILE_PLANNER_MODEL,
                "coder": CODER_MODEL,
                "critic": CRITIC_MODEL,
                "reviewer": REVIEWER_MODEL,
            },
        },
        "model_aliases": {
            "plan": sorted(PLAN_MODEL_ALIASES),
            "agent": sorted(AGENT_MODEL_ALIASES),
        },
        "settings": {
            "workspace_root": str(WORKSPACE_ROOT),
            "default_apply_mode": DEFAULT_APPLY_MODE,
            "auto_unload_after_stage": AUTO_UNLOAD_AFTER_STAGE,
            "enable_pre_apply_check": ENABLE_PRE_APPLY_CHECK,
            "enable_project_runners": ENABLE_PROJECT_RUNNERS,
            "enable_affected_graph_expansion": ENABLE_AFFECTED_GRAPH_EXPANSION,
        },
    }