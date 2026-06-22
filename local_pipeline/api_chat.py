from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .app_state import in_flight
from .client import chat_once, unload_model
from .config import (
    AUTO_UNLOAD_AFTER_STAGE,
    DUPLICATE_WINDOW_SECONDS,
    FILE_PLANNER_CTX,
    FILE_PLANNER_MODEL,
    TASK_PLANNER_CTX,
    TASK_PLANNER_MODEL,
)
from .context import normalize_apply_mode, resolve_pipeline_mode, resolve_workspace_root
from .db import save_task_state
from .pipeline_agent import run_agent_pipeline_and_stream
from .pipeline_common import load_json
from .pipeline_plan import run_plan_pipeline_and_stream
from .prompts import FILE_PLANNER_SYSTEM, TASK_PLANNER_SYSTEM
from .repository import scan_repo
from .retrieve import (
    build_repo_summary,
    build_selected_files_text,
    format_retrieved_files,
    read_selected_files,
    simple_retrieve,
)
from .schemas import TaskState
from .logging_utils import log_step

router = APIRouter()


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _latest_user_content(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _build_request_hash(content: str, repo_root: str, pipeline_mode: str) -> str:
    payload = {
        "content": content,
        "root": repo_root,
        "mode": pipeline_mode,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _default_task_plan(user_request: str) -> dict[str, Any]:
    return {
        "task_goal": user_request,
        "change_type": "unknown",
        "constraints": [],
        "success_criteria": [],
        "repo_assumptions": [],
        "search_hints": [],
    }


def _default_file_plan() -> dict[str, Any]:
    return {
        "must_read": [],
        "must_edit": [],
        "may_edit": [],
        "new_files": [],
        "edit_strategy": [],
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    user_messages = body.get("messages", [])
    if not isinstance(user_messages, list) or not user_messages:
        return _error("messages is required", 400)

    pipeline_mode = resolve_pipeline_mode(body)
    requested_model = str(body.get("model", "")).strip()
    apply_mode = normalize_apply_mode(body)
    effective_root = resolve_workspace_root(body)

    latest_user_content = _latest_user_content(user_messages)
    if not latest_user_content.strip():
        return _error("at least one user message is required", 400)

    request_hash = _build_request_hash(
        latest_user_content,
        effective_root.as_posix(),
        pipeline_mode,
    )

    now = time.perf_counter()
    started_at = in_flight.get(request_hash)
    if started_at is not None and now - started_at < DUPLICATE_WINDOW_SECONDS:
        return _error("duplicate request in flight", 429)
    in_flight[request_hash] = now

    total_start = time.perf_counter()
    task_id = str(uuid.uuid4())

    state = TaskState(
        task_id=task_id,
        created_at=time.time(),
        request_hash=request_hash,
        user_request=latest_user_content,
        repo_root=effective_root.as_posix(),
        pipeline_mode=pipeline_mode,
        requested_model=requested_model,
        apply_mode=apply_mode,
    )
    save_task_state(state)

    t = log_step(
        f"🗂️ [Workspace] root={effective_root} | mode={pipeline_mode} | apply={apply_mode}"
    )

    try:
        t = log_step(f"📂 [RepoScan] 掃描 {effective_root}...", time.perf_counter() - t)
        state.repo_manifest = scan_repo(effective_root)

        t = log_step("🔍 [Retrieve] 抓取相關檔案...", time.perf_counter() - t)
        retrieved = simple_retrieve(effective_root, latest_user_content, state.repo_manifest)
        state.retrieved_files = [asdict(item) for item in retrieved]
        retrieved_text = format_retrieved_files(retrieved)

        repo_summary = build_repo_summary(state)

        t = log_step(
            f"🧭 [TaskPlanner · {TASK_PLANNER_MODEL}] 分析任務目標...",
            time.perf_counter() - t,
        )
        task_plan_text = chat_once(
            model=TASK_PLANNER_MODEL,
            system=TASK_PLANNER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"## User Request\n{latest_user_content}\n\n"
                        f"## Repository Summary\n{repo_summary}\n\n"
                        f"## Retrieved Files\n{retrieved_text}"
                    ),
                }
            ],
            temperature=0.2,
            options={"num_ctx": TASK_PLANNER_CTX},
        )
        state.task_plan = load_json(task_plan_text, _default_task_plan(latest_user_content))
        save_task_state(state)
        if AUTO_UNLOAD_AFTER_STAGE:
            unload_model(TASK_PLANNER_MODEL)

        t = log_step(
            f"📋 [FilePlanner · {FILE_PLANNER_MODEL}] 規劃檔案操作...",
            time.perf_counter() - t,
        )
        file_plan_text = chat_once(
            model=FILE_PLANNER_MODEL,
            system=FILE_PLANNER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"## User Request\n{latest_user_content}\n\n"
                        f"## Repository Summary\n{repo_summary}\n\n"
                        f"## Retrieved Files\n{retrieved_text}\n\n"
                        f"## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}"
                    ),
                }
            ],
            temperature=0.2,
            options={"num_ctx": FILE_PLANNER_CTX},
        )
        state.file_plan = load_json(file_plan_text, _default_file_plan())
        save_task_state(state)
        if AUTO_UNLOAD_AFTER_STAGE:
            unload_model(FILE_PLANNER_MODEL)

        read_paths = list(state.file_plan.get("must_read", []) or [])
        edit_paths = list(state.file_plan.get("must_edit", []) or []) + list(
            state.file_plan.get("may_edit", []) or []
        )
        all_paths = list(dict.fromkeys(read_paths + edit_paths))
        selected_files = read_selected_files(effective_root, all_paths)
        selected_files_text = build_selected_files_text(selected_files)

        if pipeline_mode == "plan":
            return run_plan_pipeline_and_stream(
                state=state,
                latest_user_content=latest_user_content,
                selected_files_text=selected_files_text,
                repo_summary=repo_summary,
                request_hash=request_hash,
                total_start=total_start,
            )

        return run_agent_pipeline_and_stream(
            state=state,
            latest_user_content=latest_user_content,
            selected_files_text=selected_files_text,
            request_hash=request_hash,
            total_start=total_start,
        )
    except Exception:
        in_flight.pop(request_hash, None)
        raise