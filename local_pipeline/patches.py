from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .checks import pre_apply_syntax_check
from .config import PATCH_BACKUP_DIR, VALID_APPLY_MODES

"""
Patch application helpers for:
- filtering model-generated file outputs
- validating target paths
- backup / created-marker management
- atomic writes
- apply / rollback
"""


def safe_paths(file_plan: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for key in ("must_edit", "new_files", "may_edit"):
        for item in file_plan.get(key, []) or []:
            if isinstance(item, str) and item.strip():
                allowed.add(item.strip())
    return allowed


def filter_generated_files(
    files: list[dict[str, Any]],
    allowed: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for item in files:
        path = str(item.get("path", "")).strip()
        action = str(item.get("action", "")).strip()
        content = item.get("content", "")

        if path not in allowed:
            continue
        if action not in {"create", "replace"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue

        out.append(
            {
                "path": path,
                "action": action,
                "content": content,
            }
        )

    return out


def normalize_apply_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_APPLY_MODES else "dry-run"


def _is_within_root(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def validate_patch_targets(root: Path, files: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    root = root.resolve()

    for item in files:
        rel = str(item.get("path", "")).strip()
        if not rel:
            errors.append("empty patch path")
            continue

        abs_path = (root / rel).resolve()
        if not _is_within_root(root, abs_path):
            errors.append(f"path escapes workspace: {rel}")

    return errors


def _task_backup_dir(task_id: str) -> Path:
    task_dir = PATCH_BACKUP_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


def build_backup_path(task_id: str, rel_path: str) -> Path:
    rel = Path(rel_path)
    return _task_backup_dir(task_id) / "modified" / rel


def build_created_marker_path(task_id: str, rel_path: str) -> Path:
    marker_name = f"{Path(rel_path).name}.created.json"
    marker_dir = _task_backup_dir(task_id) / "created_markers" / Path(rel_path).parent
    marker_dir.mkdir(parents=True, exist_ok=True)
    return marker_dir / marker_name


def write_backup(task_id: str, rel_path: str, src_path: Path) -> str:
    backup_path = build_backup_path(task_id, rel_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, backup_path)
    return backup_path.as_posix()


def record_created_file(task_id: str, rel_path: str) -> str:
    marker = build_created_marker_path(task_id, rel_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"path": rel_path}, ensure_ascii=False),
        encoding="utf-8",
    )
    return marker.as_posix()


def atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
        ) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

        os.replace(tmp_path, target)
    except Exception:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def apply_patches(
    root: Path,
    task_id: str,
    files: list[dict[str, Any]],
    *,
    apply_mode: str,
) -> dict[str, Any]:
    normalized_apply_mode = normalize_apply_mode(apply_mode)
    root = root.resolve()

    result: dict[str, Any] = {
        "mode": normalized_apply_mode,
        "applied": [],
        "skipped": [],
        "backups": [],
        "created_markers": [],
        "errors": [],
        "precheck": pre_apply_syntax_check(root, files),
    }

    if not result["precheck"].get("ok", True):
        result["errors"].append("pre-apply syntax check failed")
        return result

    target_errors = validate_patch_targets(root, files)
    if target_errors:
        result["errors"].extend(target_errors)
        return result

    for item in files:
        rel = str(item["path"]).strip()
        action = str(item["action"]).strip()
        content = item["content"]
        abs_path = (root / rel).resolve()
        exists = abs_path.exists()

        if action == "replace" and not exists:
            result["errors"].append(f"replace target does not exist: {rel}")
            continue

        if action == "create" and exists:
            result["skipped"].append(
                {
                    "path": rel,
                    "reason": "create target already exists; treated as skipped",
                }
            )
            continue

        if normalized_apply_mode == "apply":
            if exists:
                backup_path = write_backup(task_id, rel, abs_path)
                result["backups"].append({"path": rel, "backup": backup_path})
            else:
                marker = record_created_file(task_id, rel)
                result["created_markers"].append({"path": rel, "marker": marker})

            atomic_write_text(abs_path, content)
            result["applied"].append(
                {
                    "path": rel,
                    "action": action,
                    "bytes": len(content.encode("utf-8")),
                }
            )
        else:
            result["applied"].append(
                {
                    "path": rel,
                    "action": action,
                    "bytes": len(content.encode("utf-8")),
                    "dry_run": True,
                    "would_backup": exists,
                    "would_record_created_marker": not exists,
                }
            )

    return result


def rollback_task(task_id: str, repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    backup_dir = _task_backup_dir(task_id)
    modified_dir = backup_dir / "modified"
    created_markers_dir = backup_dir / "created_markers"

    result: dict[str, Any] = {
        "task_id": task_id,
        "restored": [],
        "deleted_created": [],
        "errors": [],
    }

    if not backup_dir.exists():
        result["errors"].append("backup directory not found")
        return result

    if created_markers_dir.exists():
        for marker in sorted(created_markers_dir.rglob("*.created.json")):
            if not marker.is_file():
                continue
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                rel = str(payload["path"]).strip()
                target = (repo_root / rel).resolve()

                if not _is_within_root(repo_root, target):
                    result["errors"].append(f"rollback delete path escapes workspace: {rel}")
                    continue

                if target.exists() and target.is_file():
                    target.unlink()
                    result["deleted_created"].append({"path": rel})
            except Exception as exc:
                result["errors"].append(f"created marker parse failed: {marker.name}: {exc}")

    if modified_dir.exists():
        for backup_file in sorted(modified_dir.rglob("*")):
            if not backup_file.is_file():
                continue

            try:
                rel = backup_file.relative_to(modified_dir).as_posix()
                target = (repo_root / rel).resolve()

                if not _is_within_root(repo_root, target):
                    result["errors"].append(f"rollback path escapes workspace: {rel}")
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(
                    target,
                    backup_file.read_text(encoding="utf-8", errors="ignore"),
                )
                result["restored"].append({"path": rel, "from": backup_file.as_posix()})
            except Exception as exc:
                result["errors"].append(f"restore failed: {backup_file.as_posix()}: {exc}")

    return result


__all__ = [
    "safe_paths",
    "filter_generated_files",
    "normalize_apply_mode",
    "validate_patch_targets",
    "build_backup_path",
    "build_created_marker_path",
    "write_backup",
    "record_created_file",
    "atomic_write_text",
    "apply_patches",
    "rollback_task",
]