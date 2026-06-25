from __future__ import annotations

import asyncio
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
    CHAT_MODEL,
    FILE_PLANNER_CTX,
    FILE_PLANNER_MODEL,
    TASK_PLANNER_CTX,
    TASK_PLANNER_MODEL,
)
from .db import save_task_state
from .logging_utils import log_heartbeat, log_stage_done, log_stage_start, log_stage_unload, log_step
from .pipeline_agent import run_agent_pipeline_and_stream
from .pipeline_common import cleanup_in_flight, is_duplicate_in_flight, load_json, mark_in_flight
from .pipeline_plan import run_plan_pipeline_and_stream
from .prompts import DIRECT_CHAT_SYSTEM, FILE_PLANNER_SYSTEM, TASK_PLANNER_SYSTEM
from .repository import scan_repo
from .request_context import build_request_context
from .retrieval import (
    build_repo_summary,
    build_selected_files_text,
    format_retrieved_files,
    pick_selected_paths_from_file_plan,
    read_selected_files,
    simple_retrieve,
)
from .schemas import TaskState
from .streaming import openai_chat_completion_final, openai_error_response, openai_json_response, openai_stream_response

router = APIRouter()


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


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


def _sse_model_error(message: str, code: str = "model_response_error"):
    return openai_error_response(message, code=code)


def _openai_json_response(content: str, *, model: str, task_id: str) -> JSONResponse:
    payload = openai_json_response(content, chunk_id=f"chatcmpl-{task_id}", model=model)
    return JSONResponse(payload)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    context = build_request_context(body)

    messages = context["messages"]
    latest_user_content = context["latest_user_content"]
    pipeline_mode = context["pipeline_mode"]
    requested_model = context["requested_model"]
    apply_mode = context["apply_mode"]
    effective_root = context["effective_root"]
    request_hash = context["request_hash"]
    stream_requested = bool(body.get("stream", False))

    if not isinstance(messages, list) or not messages:
        return _error("messages is required", 400)

    if not latest_user_content.strip():
        return _error("at least one user message is required", 400)

    cleanup_in_flight(in_flight)
    if is_duplicate_in_flight(in_flight, request_hash):
        return _error("duplicate request in flight", 429)
    mark_in_flight(in_flight, request_hash)

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
    await asyncio.to_thread(save_task_state, state)

    t = log_step(f"🗂️ [Workspace] root={effective_root} | mode={pipeline_mode} | apply={apply_mode}")

    try:
        t = log_step(f"📂 [RepoScan] 掃描 {effective_root}...", time.perf_counter() - t)
        state.repo_manifest = await asyncio.to_thread(scan_repo, effective_root)

        t = log_step("🔍 [Retrieve] 抓取相關檔案...", time.perf_counter() - t)
        retrieved = await asyncio.to_thread(simple_retrieve, effective_root, latest_user_content, state.repo_manifest)
        state.retrieved_files = [asdict(item) for item in retrieved]
        retrieved_text = format_retrieved_files(retrieved)

        repo_summary = build_repo_summary(state)

        t = log_step(f"🧭 [TaskPlanner · {TASK_PLANNER_MODEL}] 分析任務目標...", time.perf_counter() - t)
        task_plan_text = await chat_once(
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
        if not task_plan_text.strip() or task_plan_text.strip() == "{}":
            return _sse_model_error(
                f"TaskPlanner model {TASK_PLANNER_MODEL} returned an empty response.",
                code="empty_task_planner_response",
            )
        state.task_plan = load_json(task_plan_text, _default_task_plan(latest_user_content))
        await asyncio.to_thread(save_task_state, state)

        if AUTO_UNLOAD_AFTER_STAGE:
            await unload_model(TASK_PLANNER_MODEL)
            log_stage_unload(TASK_PLANNER_MODEL)

        if state.task_plan.get("task_goal") == "__CHAT__":
            chat_stage_start = log_stage_start("💬 [Route]", CHAT_MODEL, "TaskPlanner 判定為簡易對話，切換到對話模型...")
            direct = await chat_once(
                model=CHAT_MODEL,
                system=DIRECT_CHAT_SYSTEM,
                messages=[{"role": "user", "content": latest_user_content}],
                temperature=0.7,
                options={"num_ctx": TASK_PLANNER_CTX},
            )
            log_stage_done("ChatModel", CHAT_MODEL, chat_stage_start)
            if not direct.strip():
                return _sse_model_error(
                    f"Direct chat model {CHAT_MODEL} returned an empty response.",
                    code="empty_direct_chat_response",
                )
            return _openai_json_response(direct, model=CHAT_MODEL, task_id=task_id)

        t = log_step(f"📋 [FilePlanner · {FILE_PLANNER_MODEL}] 規劃檔案操作...", time.perf_counter() - t)
        file_plan_text = await chat_once(
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
        if not file_plan_text.strip() or file_plan_text.strip() == "{}":
            return _sse_model_error(
                f"FilePlanner model {FILE_PLANNER_MODEL} returned an empty response.",
                code="empty_file_planner_response",
            )
        state.file_plan = load_json(file_plan_text, _default_file_plan())
        await asyncio.to_thread(save_task_state, state)

        if AUTO_UNLOAD_AFTER_STAGE:
            await unload_model(FILE_PLANNER_MODEL)
            log_stage_unload(FILE_PLANNER_MODEL)

        selected_paths = pick_selected_paths_from_file_plan(state.file_plan)
        selected_files = await asyncio.to_thread(read_selected_files, effective_root, selected_paths)
        selected_files_text = build_selected_files_text(selected_files)

        if pipeline_mode == "plan":
            return openai_stream_response(
                run_plan_pipeline_and_stream(
                    state=state,
                    latest_user_content=latest_user_content,
                    selected_files_text=selected_files_text,
                    repo_summary=repo_summary,
                    request_hash=request_hash,
                    in_flight=in_flight,
                    total_start=total_start,
                ),
                on_close=lambda: in_flight.delete(request_hash),
                chunk_id=f"chatcmpl-{task_id}",
                model=state.requested_model or requested_model or CHAT_MODEL,
            )

        if pipeline_mode == "agent":
            return openai_stream_response(
                run_agent_pipeline_and_stream(
                    state=state,
                    latest_user_content=latest_user_content,
                    selected_files_text=selected_files_text,
                    request_hash=request_hash,
                    in_flight=in_flight,
                    total_start=total_start,
                ),
                on_close=lambda: in_flight.delete(request_hash),
                chunk_id=f"chatcmpl-{task_id}",
                model=state.requested_model or requested_model or CHAT_MODEL,
            )

        if not stream_requested:
            return _openai_json_response(latest_user_content, model=state.requested_model or requested_model or CHAT_MODEL, task_id=task_id)

    except asyncio.CancelledError:
        raise
    except Exception:
        raise


__all__ = ["router"]