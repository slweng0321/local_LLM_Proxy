from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any

from .config import STATE_DB
from .schemas import TaskState

"""
SQLite persistence helpers for pipeline task state.

Responsibilities:
- initialize the tasks table
- save/load task payloads
- update stored payload status

Non-responsibilities:
- no FastAPI
- no OpenAI calls
- no filesystem patch logic
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(STATE_DB)
    con.row_factory = sqlite3.Row
    return con


def _serialize_task_state(state: TaskState) -> dict[str, Any]:
    if hasattr(state, "to_dict") and callable(state.to_dict):
        payload = state.to_dict()
        if isinstance(payload, dict):
            return payload

    if is_dataclass(state):
        payload = asdict(state)
        if isinstance(payload, dict):
            return payload

    raise TypeError("TaskState must provide to_dict() or be a dataclass")


def init_db() -> None:
    with _connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        con.commit()


def save_task_state(state: TaskState) -> None:
    payload = _serialize_task_state(state)

    with _connect() as con:
        con.execute(
            """
            INSERT INTO tasks(task_id, created_at, request_hash, status, payload_json)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json
            """,
            (
                state.task_id,
                state.created_at,
                state.request_hash,
                state.status,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        con.commit()


def load_task_payload(task_id: str) -> dict[str, Any] | None:
    with _connect() as con:
        row = con.execute(
            "SELECT payload_json FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()

    if row is None:
        return None

    payload = json.loads(row["payload_json"])
    return payload if isinstance(payload, dict) else None


def update_task_payload(task_id: str, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    with _connect() as con:
        con.execute(
            """
            UPDATE tasks
            SET status = ?, payload_json = ?
            WHERE task_id = ?
            """,
            (
                str(payload.get("status", "unknown")),
                json.dumps(payload, ensure_ascii=False),
                task_id,
            ),
        )
        con.commit()


init_db()


__all__ = [
    "init_db",
    "save_task_state",
    "load_task_payload",
    "update_task_payload",
]