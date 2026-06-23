from __future__ import annotations

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
from .logging_utils import log_step
from .patches import apply_patches, filter_generated_files, safe_paths
from .prompts import CODER_SYSTEM, CRITIC_SYSTEM, REVIEWER_SYSTEM
from .schemas import TaskState

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


def _clear_in_flight(in_flight: dict[str, float], request_hash: str) -> None:
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

    t = log_step(f"💻 [Coder · {CODER_MODEL}] 生成多檔案修改...")
    coder_start = time.perf_counter()
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

    t = log_step(f"🧪 [Critic · {CRITIC_MODEL}] 檢查 multi-file patches...", time.perf_counter() - t)
    critic_start = time.perf_counter()
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

    stage_start = time.perf_counter()
    first_chunk = True
    final_buffer: list[str] = []

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

        async for chunk in stream:
            text = chunk.choices[0].delta.content or ""
            if text:
                final_buffer.append(text)

            if first_chunk:
                print(f" ⚡ TTFT: {time.perf_counter() - stage_start:.2f}s")
                first_chunk = False

            yield f"data: {chunk.model_dump_json()}\n\n"

    except asyncio.CancelledError:
        raise
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

        _clear_in_flight(in_flight, request_hash)