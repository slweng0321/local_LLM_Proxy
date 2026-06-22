from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import (
    EXCLUDE_DIRS,
    EXCLUDE_SUFFIXES,
    MAX_FILE_BYTES,
    MAX_RETRIEVED_CHARS_PER_FILE,
    SPECIAL_SOURCE_FILENAMES,
    SOURCE_SUFFIXES,
)

"""
Repository file-level helpers.

Responsibilities:
- candidate-file filtering
- repository manifest scanning
- bounded file reading
- safe workspace path checks

Non-responsibilities:
- workspace / monorepo manifest loading
- segment detection
- affected dependency expansion
- project runner execution
- retrieval ranking / prompt formatting
- request parsing
- DB persistence
- model calls
- patch application
"""

CONFIG_OR_DOC_SUFFIXES = {
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
}


def is_candidate_file(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    if not path.is_file():
        return False

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
    except OSError:
        return False

    return (
        path.suffix.lower() in SOURCE_SUFFIXES
        or path.name.lower() in SPECIAL_SOURCE_FILENAMES
    )


def classify_repo_file(rel_path: str, suffix: str) -> str:
    lower = rel_path.lower()

    if "test" in lower or lower.startswith("tests/"):
        return "test"
    if suffix in CONFIG_OR_DOC_SUFFIXES:
        return "config_or_docs"
    return "source"


def scan_repo(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []

    for path in root.rglob("*"):
        if not is_candidate_file(path):
            continue

        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()

        files.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "suffix": suffix,
                "kind": classify_repo_file(rel, suffix),
            }
        )

    files.sort(key=lambda item: item["path"])
    return {
        "root": root.resolve().as_posix(),
        "file_count": len(files),
        "files": files[:2000],
    }


def read_file_snippet(
    path: Path,
    max_chars: int = MAX_RETRIEVED_CHARS_PER_FILE,
) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[:max_chars]


def is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def resolve_repo_path(root: Path, rel_path: str) -> Path | None:
    rel = rel_path.strip()
    if not rel:
        return None

    resolved_root = root.resolve()
    abs_path = (resolved_root / rel).resolve()

    if not is_within_root(abs_path, resolved_root):
        return None

    return abs_path


def read_existing_text_file(
    root: Path,
    rel_path: str,
    *,
    max_chars: int = MAX_RETRIEVED_CHARS_PER_FILE,
) -> str | None:
    abs_path = resolve_repo_path(root, rel_path)
    if abs_path is None or not abs_path.exists() or not abs_path.is_file():
        return None
    return read_file_snippet(abs_path, max_chars=max_chars)


def read_existing_files(
    root: Path,
    paths: list[str],
    *,
    max_chars: int = MAX_RETRIEVED_CHARS_PER_FILE,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    for rel in paths:
        rel = rel.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)

        content = read_existing_text_file(root, rel, max_chars=max_chars)
        if content is None:
            continue

        out.append(
            {
                "path": rel,
                "content": content,
            }
        )

    return out


__all__ = [
    "is_candidate_file",
    "classify_repo_file",
    "scan_repo",
    "read_file_snippet",
    "is_within_root",
    "resolve_repo_path",
    "read_existing_text_file",
    "read_existing_files",
]