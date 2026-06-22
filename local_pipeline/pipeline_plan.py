from __future__ import annotations

import json
import time
from typing import Any

from fastapi.responses import StreamingResponse

from .client import client, load_json, unload_model
from .config import (
    AUTO_UNLOAD_AFTER_STAGE,
    PLAN_REVIEWER_CTX,
    PLAN_REVIEWER_MODEL,
    PLAN_REVIEWER_PREDICT,
    REQUEST_TIMEOUT,
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


def run_plan_pipeline_and_stream(
    *,
    state: TaskState,
    latest_user_content: str,
    selected_files_text: str,
    repo_summary: str,
    request_hash: str,
    in_flight: dict[str, float],
    total_start: float,
) -> StreamingResponse:
    log_step(f"📝 [PlanReviewer · {PLAN_REVIEWER_MODEL}] 產生唯讀計畫並串流...")

    stream_response = client.chat.completions.create(
        model=PLAN_REVIEWER_MODEL,
        messages=[
            {"role": "system", "content": PLAN_REVIEWER_SYSTEM},
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
        stream=True,
        extra_body={
            "options": {
                "num_ctx": PLAN_REVIEWER_CTX,
                "num_predict": PLAN_REVIEWER_PREDICT,
            }
        },
        timeout=REQUEST_TIMEOUT,
    )

    stage_start = time.perf_counter()
    first_chunk = True
    final_buffer: list[str] = []

    def stream_generator():
        nonlocal first_chunk

        try:
            for chunk in stream_response:
                text = chunk.choices[0].delta.content or ""
                if text:
                    final_buffer.append(text)

                if first_chunk:
                    print(f" ⚡ TTFT: {time.perf_counter() - stage_start:.2f}s")
                    first_chunk = False

                yield f"data: {chunk.model_dump_json()}\n\n"

        finally:
            try:
                full_text = "".join(final_buffer)
                state.plan_result = load_json(full_text, PLAN_RESULT_DEFAULT.copy())
                state.status = "planned"
                state.metrics["plan_reviewer_elapsed_sec"] = round(
                    time.perf_counter() - stage_start,
                    2,
                )
                state.metrics["total_elapsed_sec"] = round(
                    time.perf_counter() - total_start,
                    2,
                )
                save_task_state(state)
            finally:
                if AUTO_UNLOAD_AFTER_STAGE:
                    unload_model(PLAN_REVIEWER_MODEL)

                _clear_in_flight(in_flight, request_hash)
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "PLAN_RESULT_DEFAULT",
    "run_plan_pipeline_and_stream",
]