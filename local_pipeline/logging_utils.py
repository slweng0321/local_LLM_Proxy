from __future__ import annotations

import time

"""
Small logging helpers for pipeline stage timing.

Responsibilities:
- print stage labels consistently
- measure elapsed seconds between stages
- stay dependency-free

Non-responsibilities:
- no logging framework setup
- no file logging
- no FastAPI / database / pipeline logic
"""


def log_step(label: str, elapsed: float | None = None) -> float:
    if elapsed is not None:
        print(f" ⏱ 耗時 {elapsed:.2f}s")
    print(f"\n{label}")
    return time.perf_counter()


def log_elapsed(label: str, started_at: float) -> float:
    elapsed = time.perf_counter() - started_at
    print(f" ⏱ {label}: {elapsed:.2f}s")
    return elapsed


def elapsed_since(started_at: float) -> float:
    return time.perf_counter() - started_at


def _log(label: str, elapsed: float | None = None) -> float:
    return log_step(label, elapsed)


__all__ = [
    "log_step",
    "log_elapsed",
    "elapsed_since",
]