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
from .logging_utils import log_step
from .prompts import PLAN_REVIEWER_SYSTEM
from .schemas import TaskState

PLAN_RESULT_DEFAULT: dict[str, Any] = {
    "summary": [],
    "intent": "",
    "diagnosis": [],
    "recommended_steps": [],
    "candidate_files": [],
    "risks": [],
    "suggested_apply_mode": "dry-run",
}


def _clear_in_flight(in_flight: dict[str, float], request_hash: str) -> None:
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
    log_step(f"📝 [PlanReviewer · {PLAN_REVIEWER_MODEL}] 產生唯讀計畫並串流...")

    stage_start = time.perf_counter()
    first_chunk = True
    final_buffer: list[str] = []

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
        state.plan_result = load_json(full_text, PLAN_RESULT_DEFAULT.copy())
        state.status = "planned"
        state.metrics["plan_reviewer_elapsed_sec"] = round(time.perf_counter() - stage_start, 2)
        state.metrics["total_elapsed_sec"] = round(time.perf_counter() - total_start, 2)
        await asyncio.to_thread(save_task_state, state)

        if AUTO_UNLOAD_AFTER_STAGE:
            await unload_model(PLAN_REVIEWER_MODEL)

        _clear_in_flight(in_flight, request_hash)