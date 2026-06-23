from __future__ import annotations

import os
from pathlib import Path

"""
Project-wide configuration constants.

Responsibilities:
- environment variable parsing
- model aliases
- context window / prediction settings
- timeout and feature flags
- repository scanning constants
- state directory paths
"""

VALID_APPLY_MODES = {
    "dry-run",
    "apply",
}


def _get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_apply_mode(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in VALID_APPLY_MODES else "dry-run"


OLLAMA_BASE = _get_env_str("OLLAMA_BASE", "http://localhost:11434").rstrip("/")
OPENAI_BASE = f"{OLLAMA_BASE}/v1"

WORKSPACE_ROOT = Path(_get_env_str("WORKSPACE_ROOT", ".")).resolve()
STATE_DIR = Path(_get_env_str("STATE_DIR", ".repo_aware_state")).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

STATE_DB = STATE_DIR / "tasks.sqlite3"
PATCH_BACKUP_DIR = STATE_DIR / "backups"
PATCH_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_APPLY_MODE = _normalize_apply_mode(_get_env_str("DEFAULT_APPLY_MODE", "dry-run"))

ENABLE_PRE_APPLY_CHECK = _get_env_bool("ENABLE_PRE_APPLY_CHECK", True)
PRECHECK_TIMEOUT = _get_env_int("PRECHECK_TIMEOUT", 20)

PROJECT_RUNNER_TIMEOUT = _get_env_int("PROJECT_RUNNER_TIMEOUT", 120)
ENABLE_PROJECT_RUNNERS = _get_env_bool("ENABLE_PROJECT_RUNNERS", True)
ENABLE_AFFECTED_GRAPH_EXPANSION = _get_env_bool("ENABLE_AFFECTED_GRAPH_EXPANSION", True)

AUTO_UNLOAD_AFTER_STAGE = _get_env_bool("AUTO_UNLOAD_AFTER_STAGE", True)
REQUEST_TIMEOUT = _get_env_int("REQUEST_TIMEOUT", 600)
DUPLICATE_WINDOW_SECONDS = _get_env_int("DUPLICATE_WINDOW_SECONDS", 8)

CHAT_MODEL = _get_env_str("CHAT_MODEL", "qwen3.5:4b")
TASK_PLANNER_MODEL = _get_env_str("TASK_PLANNER_MODEL", "qwen2.5:3b-instruct")
FILE_PLANNER_MODEL = _get_env_str("FILE_PLANNER_MODEL", "qwen2.5:3b-instruct")
CODER_MODEL = _get_env_str("CODER_MODEL", "qwen2.5-coder:7b")
CRITIC_MODEL = _get_env_str("CRITIC_MODEL", "qwen2.5-coder:3b-instruct")
REVIEWER_MODEL = _get_env_str("REVIEWER_MODEL", "qwen2.5-coder:7b")
PLAN_REVIEWER_MODEL = _get_env_str("PLAN_REVIEWER_MODEL", REVIEWER_MODEL)

TASK_PLANNER_CTX = _get_env_int("TASK_PLANNER_CTX", 4096)
FILE_PLANNER_CTX = _get_env_int("FILE_PLANNER_CTX", 6144)
CODER_CTX = _get_env_int("CODER_CTX", 8192)
CODER_PREDICT = _get_env_int("CODER_PREDICT", 4096)
CRITIC_CTX = _get_env_int("CRITIC_CTX", 8192)
REVIEWER_CTX = _get_env_int("REVIEWER_CTX", 8192)
REVIEWER_PREDICT = _get_env_int("REVIEWER_PREDICT", 4096)
PLAN_REVIEWER_CTX = _get_env_int("PLAN_REVIEWER_CTX", 6144)
PLAN_REVIEWER_PREDICT = _get_env_int("PLAN_REVIEWER_PREDICT", 2048)

MAX_FILE_BYTES = _get_env_int("MAX_FILE_BYTES", 120_000)
MAX_RETRIEVED_FILES = _get_env_int("MAX_RETRIEVED_FILES", 8)
MAX_RETRIEVED_CHARS_PER_FILE = _get_env_int("MAX_RETRIEVED_CHARS_PER_FILE", 12_000)

PLAN_MODEL_ALIASES = {
    "local-pipeline-plan",
    "plan",
}

AGENT_MODEL_ALIASES = {
    "local-pipeline-agent",
    "agent",
    "multi-agent",
}

EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".nuxt",
    ".repo_aware_state",
    ".turbo",
}

EXCLUDE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".mp4",
    ".mp3",
    ".wav",
    ".lock",
    ".pyc",
    ".pyo",
}

SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".kt",
    ".m",
    ".scala",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".md",
    ".sql",
    ".sh",
}

SPECIAL_SOURCE_FILENAMES = {
    "readme",
    "dockerfile",
    "makefile",
}

__all__ = [
    "OLLAMA_BASE",
    "OPENAI_BASE",
    "WORKSPACE_ROOT",
    "STATE_DIR",
    "STATE_DB",
    "PATCH_BACKUP_DIR",
    "DEFAULT_APPLY_MODE",
    "VALID_APPLY_MODES",
    "ENABLE_PRE_APPLY_CHECK",
    "PRECHECK_TIMEOUT",
    "PROJECT_RUNNER_TIMEOUT",
    "ENABLE_PROJECT_RUNNERS",
    "ENABLE_AFFECTED_GRAPH_EXPANSION",
    "AUTO_UNLOAD_AFTER_STAGE",
    "REQUEST_TIMEOUT",
    "DUPLICATE_WINDOW_SECONDS",
    "CHAT_MODEL",
    "TASK_PLANNER_MODEL",
    "FILE_PLANNER_MODEL",
    "CODER_MODEL",
    "CRITIC_MODEL",
    "REVIEWER_MODEL",
    "PLAN_REVIEWER_MODEL",
    "TASK_PLANNER_CTX",
    "FILE_PLANNER_CTX",
    "CODER_CTX",
    "CODER_PREDICT",
    "CRITIC_CTX",
    "REVIEWER_CTX",
    "REVIEWER_PREDICT",
    "PLAN_REVIEWER_CTX",
    "PLAN_REVIEWER_PREDICT",
    "MAX_FILE_BYTES",
    "MAX_RETRIEVED_FILES",
    "MAX_RETRIEVED_CHARS_PER_FILE",
    "PLAN_MODEL_ALIASES",
    "AGENT_MODEL_ALIASES",
    "EXCLUDE_DIRS",
    "EXCLUDE_SUFFIXES",
    "SOURCE_SUFFIXES",
    "SPECIAL_SOURCE_FILENAMES",
]