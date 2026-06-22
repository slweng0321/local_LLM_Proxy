from __future__ import annotations

import json
import os
import py_compile
import subprocess
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:
    yaml = None

from .config import ENABLE_PRE_APPLY_CHECK, PRECHECK_TIMEOUT

"""
Pre-apply validation helpers.

Responsibilities:
- file-level syntax / parse validation for generated content
- shared subprocess execution helper
- orchestration of segmented project runners after file-level checks pass

Non-responsibilities:
- patch application
- repository scanning / retrieval
- workspace graph construction
- API request handling
"""


def run_subprocess(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout or PRECHECK_TIMEOUT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, output.strip()
    except Exception as exc:
        return False, str(exc)


def _check_python(rel_path: str, content: str) -> tuple[bool, str]:
    try:
        compile(content, rel_path, "exec")
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".py",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            py_compile.compile(tmp_path, doraise=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _check_json_content(content: str) -> tuple[bool, str]:
    try:
        json.loads(content)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _check_node_syntax(content: str, suffix: str) -> tuple[bool, str]:
    ext = suffix if suffix in {".js", ".mjs", ".cjs"} else ".js"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=ext,
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return run_subprocess(["node", "--check", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _check_typescript(content: str, suffix: str) -> tuple[bool, str]:
    ext = suffix if suffix in {".ts", ".tsx"} else ".ts"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=ext,
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return run_subprocess(["tsc", "--noEmit", "--pretty", "false", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _check_shell(content: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".sh",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return run_subprocess(["bash", "-n", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _check_toml(content: str) -> tuple[bool, str]:
    try:
        import tomllib

        tomllib.loads(content)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _check_yaml(content: str) -> tuple[bool, str]:
    try:
        if yaml is None:
            return False, "PyYAML not installed"
        yaml.safe_load(content)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _check_ini(content: str) -> tuple[bool, str]:
    try:
        import configparser

        parser = configparser.ConfigParser()
        parser.read_string(content)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _run_file_level_check(rel_path: str, content: str) -> tuple[bool | None, str, str]:
    suffix = Path(rel_path).suffix.lower()

    if suffix == ".py":
        ok, detail = _check_python(rel_path, content)
        return ok, "python", detail
    if suffix == ".json":
        ok, detail = _check_json_content(content)
        return ok, "json", detail
    if suffix in {".yaml", ".yml"}:
        ok, detail = _check_yaml(content)
        return ok, "yaml", detail
    if suffix == ".toml":
        ok, detail = _check_toml(content)
        return ok, "toml", detail
    if suffix == ".ini":
        ok, detail = _check_ini(content)
        return ok, "ini", detail
    if suffix == ".sh":
        ok, detail = _check_shell(content)
        return ok, "bash -n", detail
    if suffix in {".js", ".mjs", ".cjs"}:
        ok, detail = _check_node_syntax(content, suffix)
        return ok, "node --check", detail
    if suffix in {".ts", ".tsx"}:
        ok, detail = _check_typescript(content, suffix)
        return ok, "tsc --noEmit", detail

    return None, "none", f"no file-level precheck runner for {suffix or 'no-suffix'}"


def _validate_generated_item(item: dict[str, Any]) -> tuple[bool, str, str]:
    rel_path = str(item.get("path", "")).strip()
    if not rel_path:
        return False, "", "missing path in generated file item"

    content = item.get("content", "")
    if not isinstance(content, str):
        return False, rel_path, "content must be a string"

    return True, rel_path, ""


def pre_apply_syntax_check(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    from .workspace_graph import run_segmented_project_runners

    result: dict[str, Any] = {
        "ok": True,
        "checked": [],
        "errors": [],
        "skipped": [],
        "project_runner": {},
    }

    if not ENABLE_PRE_APPLY_CHECK:
        return result

    for item in files:
        valid, rel_path, validation_error = _validate_generated_item(item)
        if not valid:
            result["ok"] = False
            result["errors"].append(
                {
                    "path": rel_path,
                    "runner": "input-validation",
                    "error": validation_error,
                }
            )
            continue

        content = item["content"]
        ok, runner, detail = _run_file_level_check(rel_path, content)

        if ok is None:
            result["skipped"].append({"path": rel_path, "reason": detail})
            continue

        if ok:
            result["checked"].append({"path": rel_path, "runner": runner})
        else:
            result["ok"] = False
            result["errors"].append(
                {
                    "path": rel_path,
                    "runner": runner,
                    "error": detail,
                }
            )

    if result["ok"]:
        project_runner_result = run_segmented_project_runners(root, files)
        result["project_runner"] = project_runner_result

        if project_runner_result.get("errors"):
            result["ok"] = False
            result["errors"].append(
                {
                    "path": "",
                    "runner": "affected-graph-aware-segmented-profile",
                    "error": "one or more segmented project runners failed",
                }
            )

    return result


__all__ = [
    "run_subprocess",
    "pre_apply_syntax_check",
]