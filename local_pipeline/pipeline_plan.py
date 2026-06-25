from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from .client import chat_stream, load_json, unload_model
from .config import (
    AUTO_UNLOAD_AFTER_STAGE,
    PLAN_REVIEWER_CTX,
    PLAN_REVIEWER_MODEL,
    PLAN_REVIEWER_PREDICT,
)
from .db import save_task_state
from .logging_utils import log_heartbeat, log_stage_done, log_stage_start, log_stage_unload, log_step, log_ttft
from .prompts import PLAN_REVIEWER_SYSTEM
from .schemas import TaskState
from .streaming import format_chunk, openai_chat_completion_chunk, openai_chat_completion_final

PLAN_RESULT_DEFAULT: dict[str, Any] = {
    "summary": [],
    "intent": "",
    "diagnosis": [],
    "recommended_steps": [],
    "candidate_files": [],
    "risks": [],
    "suggested_apply_mode": "dry-run",
}


def _sse_error(message: str, code: str) -> AsyncIterator[str]:
    async def iterator():
        yield format_chunk(f"\n> ❌ **Pipeline 錯誤**: {message}\n\n")
        yield "[DONE]"

    return iterator()


def _clear_in_flight(in_flight: Any, request_hash: str) -> None:
    if hasattr(in_flight, "delete"):
        in_flight.delete(request_hash)
        return
    in_flight.pop(request_hash, None)


async def run_plan_pipeline_and_stream(
    *,
    state: TaskState,
    latest_user_content: str,
    selected_files_text: str,
    repo_summary: str,
    request_hash: str,
    in_flight: dict[str, float],
    total_start: float,
) -> AsyncIterator[str]:
    stage_start = log_stage_start("📝 [PlanReviewer]", PLAN_REVIEWER_MODEL, "開始產生唯讀計畫並串流...")
    first_chunk = True
    final_buffer: list[str] = []
    completed_normally = False

    yield f"data: {log_heartbeat('正在進行 TaskPlanner / FilePlanner 分析...')}"
    yield f"data: {log_heartbeat('TaskPlanner 完成，準備進入 FilePlanner / PlanReviewer...')}"

    try:
        stream = await chat_stream(
            model=PLAN_REVIEWER_MODEL,
            system=PLAN_REVIEWER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"## User Request\n{latest_user_content}\n\n"
                        f"## Repository Summary\n{repo_summary}\n\n"
                        f"## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n"
                        f"## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n"
                        f"## Selected Files\n{selected_files_text}"
                    ),
                },
            ],
            temperature=0.1,
            options={"num_ctx": PLAN_REVIEWER_CTX, "num_predict": PLAN_REVIEWER_PREDICT},
        )

        if stream is None:
            async for chunk in _sse_error(
                f"PlanReviewer model {PLAN_REVIEWER_MODEL} returned no stream.",
                "empty_plan_reviewer_stream",
            ):
                yield chunk
            return

        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                async for err_chunk in _sse_error(
                    f"PlanReviewer model {PLAN_REVIEWER_MODEL} returned a chunk without choices.",
                    "plan_reviewer_no_choices",
                ):
                    yield err_chunk
                return

            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) or ""
            if text:
                final_buffer.append(text)

            if first_chunk:
                log_ttft(stage_start)
                first_chunk = False

            if text:
                yield format_chunk(text, chunk_id=f"chatcmpl-{state.task_id}", model=PLAN_REVIEWER_MODEL)

        completed_normally = True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        async for chunk in _sse_error(f"Plan pipeline failed: {exc}", "plan_pipeline_exception"):
            yield chunk
        return
    finally:
        full_text = "".join(final_buffer)
        state.plan_result = load_json(full_text, PLAN_RESULT_DEFAULT.copy())
        state.status = "planned"
        state.metrics["plan_reviewer_elapsed_sec"] = round(time.perf_counter() - stage_start, 2)
        state.metrics["total_elapsed_sec"] = round(time.perf_counter() - total_start, 2)
        await asyncio.to_thread(save_task_state, state)

        if AUTO_UNLOAD_AFTER_STAGE:
            await unload_model(PLAN_REVIEWER_MODEL)
            log_stage_unload(PLAN_REVIEWER_MODEL)

        log_stage_done("PlanReviewer", PLAN_REVIEWER_MODEL, stage_start)

        try:
            pass
        finally:
            _clear_in_flight(in_flight, request_hash)


def build_plan_completion_payload(state: TaskState, content: str) -> str:
    return openai_chat_completion_final(
        chunk_id=f"chatcmpl-{state.task_id}",
        model=PLAN_REVIEWER_MODEL,
        content=content,
    )