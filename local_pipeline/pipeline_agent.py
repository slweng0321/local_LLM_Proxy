from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .client import chat_once, chat_stream, load_json, unload_model
from .config import (
    AUTO_UNLOAD_AFTER_STAGE,
    CODER_CTX,
    CODER_MODEL,
    CODER_PREDICT,
    CRITIC_CTX,
    CRITIC_MODEL,
    REVIEWER_CTX,
    REVIEWER_MODEL,
    REVIEWER_PREDICT,
)
from .db import save_task_state
from .logging_utils import log_heartbeat, log_stage_done, log_stage_start, log_stage_unload, log_step, log_ttft
from .patches import apply_patches, filter_generated_files, safe_paths
from .prompts import CODER_SYSTEM, CRITIC_SYSTEM, REVIEWER_SYSTEM
from .schemas import TaskState
from .streaming import format_chunk, openai_chat_completion_chunk, openai_chat_completion_final

CODER_RESULT_DEFAULT: dict[str, Any] = {
    "files": [],
    "notes": [],
}

CRITIC_RESULT_DEFAULT: dict[str, Any] = {
    "acceptable": False,
    "must_fix": [],
    "optional_improvements": [],
    "reviewer_instruction": "",
}

REVIEWER_RESULT_DEFAULT: dict[str, Any] = {
    "files": [],
    "summary": [],
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


def _build_status_after_apply(state: TaskState) -> str:
    apply_errors = state.apply_result.get("errors", []) if isinstance(state.apply_result, dict) else []
    if apply_errors:
        return "apply_failed"
    if state.apply_mode == "apply":
        return "applied"
    return "dry_run_complete"


async def run_agent_pipeline_and_stream(
    *,
    state: TaskState,
    latest_user_content: str,
    selected_files_text: str,
    request_hash: str,
    in_flight: dict[str, float],
    total_start: float,
) -> AsyncIterator[str]:
    allowed_paths = safe_paths(state.file_plan)

    yield f"data: {log_heartbeat('正在生成 Coder 提案...')}"

    coder_start = log_stage_start("💻 [Coder]", CODER_MODEL, "開始生成多檔案修改...")
    coder_text = await chat_once(
        model=CODER_MODEL,
        system=CODER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"## User Request\n{latest_user_content}\n\n"
                    f"## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n"
                    f"## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n"
                    f"## Selected Files\n{selected_files_text}"
                ),
            }
        ],
        temperature=0.15,
        options={"num_ctx": CODER_CTX, "num_predict": CODER_PREDICT},
    )
    coder_json = load_json(coder_text, CODER_RESULT_DEFAULT.copy())
    state.generated_files = filter_generated_files(coder_json.get("files", []), allowed_paths)
    state.status = "coded"
    state.metrics["coder_elapsed_sec"] = round(time.perf_counter() - coder_start, 2)
    await asyncio.to_thread(save_task_state, state)

    if AUTO_UNLOAD_AFTER_STAGE:
        await unload_model(CODER_MODEL)
        log_stage_unload(CODER_MODEL)

    yield f"data: {log_heartbeat('正在進行 Critic 審查...')}"
    critic_start = log_stage_start("🧪 [Critic]", CRITIC_MODEL, "開始檢查 multi-file patches...")
    critic_text = await chat_once(
        model=CRITIC_MODEL,
        system=CRITIC_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"## User Request\n{latest_user_content}\n\n"
                    f"## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n"
                    f"## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n"
                    f"## Original Selected Files\n{selected_files_text}\n\n"
                    f"## Generated Files\n{json.dumps(state.generated_files, ensure_ascii=False)}"
                ),
            }
        ],
        temperature=0.1,
        options={"num_ctx": CRITIC_CTX},
    )
    state.critic_report = load_json(critic_text, CRITIC_RESULT_DEFAULT.copy())
    state.status = "critic_done"
    state.metrics["critic_elapsed_sec"] = round(time.perf_counter() - critic_start, 2)
    await asyncio.to_thread(save_task_state, state)

    if AUTO_UNLOAD_AFTER_STAGE:
        await unload_model(CRITIC_MODEL)
        log_stage_unload(CRITIC_MODEL)

    yield f"data: {log_heartbeat('正在生成 Reviewer 最終回應...')}"
    stage_start = time.perf_counter()
    first_chunk = True
    final_buffer: list[str] = []
    completed_normally = False

    reviewer_stage_start = log_stage_start("📦 [Reviewer]", REVIEWER_MODEL, "開始串流最終回應...")

    try:
        stream = await chat_stream(
            model=REVIEWER_MODEL,
            system=REVIEWER_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"## User Request\n{latest_user_content}\n\n"
                        f"## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n"
                        f"## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n"
                        f"## Original Selected Files\n{selected_files_text}\n\n"
                        f"## Generated Files\n{json.dumps(state.generated_files, ensure_ascii=False)}\n\n"
                        f"## Critic Report\n{json.dumps(state.critic_report, ensure_ascii=False)}"
                    ),
                }
            ],
            temperature=0.15,
            options={"num_ctx": REVIEWER_CTX, "num_predict": REVIEWER_PREDICT},
        )

        if stream is None:
            async for chunk in _sse_error(
                f"Reviewer model {REVIEWER_MODEL} returned no stream.",
                "empty_reviewer_stream",
            ):
                yield chunk
            return

        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                async for err_chunk in _sse_error(
                    f"Reviewer model {REVIEWER_MODEL} returned a chunk without choices.",
                    "reviewer_no_choices",
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
                yield format_chunk(text, chunk_id=f"chatcmpl-{state.task_id}", model=REVIEWER_MODEL)

        completed_normally = True

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        async for chunk in _sse_error(f"Agent pipeline failed: {exc}", "agent_pipeline_exception"):
            yield chunk
        return
    finally:
        full_text = "".join(final_buffer)
        reviewer_json = load_json(full_text, REVIEWER_RESULT_DEFAULT.copy())

        state.final_files = filter_generated_files(
            reviewer_json.get("files", []),
            allowed_paths,
        )
        state.status = "reviewed"

        state.apply_result = await asyncio.to_thread(
            apply_patches,
            Path(state.repo_root),
            state.task_id,
            state.final_files,
            apply_mode=state.apply_mode,
        )
        state.status = _build_status_after_apply(state)
        state.metrics["reviewer_stream_elapsed_sec"] = round(time.perf_counter() - stage_start, 2)
        state.metrics["total_elapsed_sec"] = round(time.perf_counter() - total_start, 2)
        await asyncio.to_thread(save_task_state, state)

        if AUTO_UNLOAD_AFTER_STAGE:
            await unload_model(REVIEWER_MODEL)
            log_stage_unload(REVIEWER_MODEL)

        log_stage_done("Reviewer", REVIEWER_MODEL, reviewer_stage_start)

        try:
            pass
        finally:
            _clear_in_flight(in_flight, request_hash)


def build_agent_completion_payload(state: TaskState, content: str) -> str:
    return openai_chat_completion_final(
        chunk_id=f"chatcmpl-{state.task_id}",
        model=REVIEWER_MODEL,
        content=content,
    )