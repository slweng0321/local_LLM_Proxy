from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import MAX_RETRIEVED_FILES
from .pipeline_common import coerce_str_list, merge_path_lists
from .repository import read_existing_files, read_file_snippet
from .schemas import RetrievedFile, TaskState

"""
Retrieval helpers.

Responsibilities:
- retrieve likely relevant files from a scanned repo manifest
- safely read selected files from workspace
- format retrieved/selected files into prompt-ready text blocks
- build compact repository summary text for planner/reviewer stages

Non-responsibilities:
- repository scanning
- workspace / monorepo detection
- request parsing
- DB persistence
- model calls
"""


def _tokenize_request_for_retrieval(user_request: str) -> set[str]:
    normalized = (
        user_request.replace("/", " ")
        .replace("_", " ")
        .replace("-", " ")
        .strip()
    )
    return {tok.lower() for tok in normalized.split() if len(tok) >= 3}


def simple_retrieve(
    root: Path,
    user_request: str,
    manifest: dict[str, Any],
    top_k: int = MAX_RETRIEVED_FILES,
) -> list[RetrievedFile]:
    tokens = _tokenize_request_for_retrieval(user_request)
    scored: list[tuple[int, str]] = []

    for item in manifest.get("files", []):
        rel_path = str(item.get("path", "")).strip()
        if not rel_path:
            continue

        kind = str(item.get("kind", "")).strip()
        lower = rel_path.lower()
        score = 0

        for tok in tokens:
            if tok in lower:
                score += 5

        if kind == "source":
            score += 2
        if kind == "test" and ("test" in user_request.lower() or "測試" in user_request):
            score += 3
        if lower.endswith("readme.md"):
            score += 2

        if score > 0:
            scored.append((score, rel_path))

    scored.sort(key=lambda item: (-item[0], item[1]))

    selected: list[RetrievedFile] = []
    seen: set[str] = set()

    for score, rel in scored:
        if rel in seen:
            continue
        seen.add(rel)

        selected.append(
            RetrievedFile(
                path=rel,
                reason=f"score={score}",
                preview=read_file_snippet(root / rel),
            )
        )
        if len(selected) >= top_k:
            break

    if selected:
        return selected

    for item in manifest.get("files", [])[: min(top_k, 5)]:
        rel = str(item.get("path", "")).strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)

        selected.append(
            RetrievedFile(
                path=rel,
                reason="fallback",
                preview=read_file_snippet(root / rel),
            )
        )

    return selected


def format_retrieved_files(files: list[RetrievedFile]) -> str:
    parts: list[str] = []

    for item in files:
        parts.append(
            f"## FILE: {item.path}\n"
            f"Reason: {item.reason}\n"
            f"```\n{item.preview}\n```"
        )

    return "\n\n".join(parts)


def read_selected_files(root: Path, paths: list[str]) -> list[dict[str, str]]:
    return read_existing_files(root, paths)


def build_repo_summary(state: TaskState, *, max_files: int = 80) -> str:
    manifest = state.repo_manifest if isinstance(state.repo_manifest, dict) else {}
    return json.dumps(
        {
            "root": manifest.get("root"),
            "file_count": manifest.get("file_count"),
            "files": manifest.get("files", [])[:max_files],
        },
        ensure_ascii=False,
    )


def build_selected_files_text(selected_files: list[dict[str, str]]) -> str:
    blocks: list[str] = []

    for item in selected_files:
        path = str(item.get("path", "")).strip()
        content = item.get("content", "")
        if not path:
            continue
        if not isinstance(content, str):
            content = str(content)

        blocks.append(f"## FILE: {path}\n```\n{content}\n```")

    return "\n\n".join(blocks)


def pick_selected_paths_from_file_plan(file_plan: dict[str, Any]) -> list[str]:
    if not isinstance(file_plan, dict):
        return []

    return merge_path_lists(
        coerce_str_list(file_plan.get("must_read")),
        coerce_str_list(file_plan.get("must_edit")),
        coerce_str_list(file_plan.get("may_edit")),
    )


def load_selected_files_from_state(root: Path, state: TaskState) -> list[dict[str, str]]:
    selected_paths = pick_selected_paths_from_file_plan(state.file_plan)
    return read_selected_files(root, selected_paths)


def build_retrieval_bundle(
    *,
    root: Path,
    state: TaskState,
    user_request: str,
) -> dict[str, Any]:
    retrieved = simple_retrieve(root, user_request, state.repo_manifest)
    selected_paths = pick_selected_paths_from_file_plan(state.file_plan)
    selected_files = read_selected_files(root, selected_paths)

    return {
        "retrieved_files": retrieved,
        "retrieved_files_text": format_retrieved_files(retrieved),
        "selected_paths": selected_paths,
        "selected_files": selected_files,
        "selected_files_text": build_selected_files_text(selected_files),
        "repo_summary": build_repo_summary(state),
    }


__all__ = [
    "simple_retrieve",
    "format_retrieved_files",
    "read_selected_files",
    "build_repo_summary",
    "build_selected_files_text",
    "pick_selected_paths_from_file_plan",
    "load_selected_files_from_state",
    "build_retrieval_bundle",
]